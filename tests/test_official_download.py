"""公式取得の保存・再試行・履歴整合性を、実通信なしで検証する。"""

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import cast

import pytest

import boatrace.official_download as downloader
from boatrace.official_download import (
    DownloadPolicy,
    DownloadReport,
    DownloadStatus,
    HistoryWriteError,
    download_official_file,
)
from boatrace.official_files import OfficialDataKind, build_official_file_spec
from boatrace.official_http import TransferError, TransferPolicy, TransferResult

SAMPLE_DATE = date(2026, 9, 2)
PAYLOAD = b"test archive bytes; the external validator is replaced in these unit tests"


@dataclass
class Harness:
    root: Path
    seven_zip: Path
    replies: list[bytes | Exception] = field(default_factory=lambda: [PAYLOAD])
    urls: list[str] = field(default_factory=list[str])
    sleeps: list[float] = field(default_factory=list[float])
    commands: list[list[str]] = field(default_factory=list[list[str]])
    validator_exit: int = 0
    validator_timeout: bool = False

    @property
    def archive(self) -> Path:
        return build_official_file_spec(
            self.root, SAMPLE_DATE, OfficialDataKind.RESULT
        ).archive_path

    def run(self, policy: DownloadPolicy | None = None) -> DownloadReport:
        return download_official_file(
            self.root, SAMPLE_DATE, OfficialDataKind.RESULT, self.seven_zip, policy=policy
        )

    def fetch(self, url: str, destination: Path, policy: TransferPolicy) -> TransferResult:
        self.urls.append(url)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            destination.write_bytes(b"partial")
            raise reply
        destination.write_bytes(reply)
        return TransferResult(200, len(reply), hashlib.sha256(reply).hexdigest())

    def validate(
        self,
        command: list[str],
        *,
        stdin: int,
        capture_output: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(command)
        assert stdin == subprocess.DEVNULL
        assert capture_output and not check
        assert timeout > 0
        if self.validator_timeout:
            raise subprocess.TimeoutExpired(command, timeout, output=b"partial output")
        return subprocess.CompletedProcess(command, self.validator_exit, b"validator output", b"")


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    seven_zip = tmp_path / "7z.exe"
    seven_zip.write_bytes(b"placeholder")
    result = Harness(tmp_path / "BoatRaceData", seven_zip)
    monkeypatch.setattr(downloader, "fetch_archive_once", result.fetch)
    monkeypatch.setattr(downloader.subprocess, "run", result.validate)
    monkeypatch.setattr(downloader.time, "sleep", result.sleeps.append)
    return result


def read_json(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def test_download_validates_publishes_and_records_provenance(harness: Harness) -> None:
    report = harness.run()

    assert report.status is DownloadStatus.DOWNLOADED
    assert harness.archive.read_bytes() == PAYLOAD
    assert report.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert report.size_bytes == len(PAYLOAD)
    assert report.acquired_at is not None
    assert datetime.fromisoformat(report.acquired_at).utcoffset() is not None
    assert len(report.implementation_sha256) == 64
    assert harness.urls == ["https://www1.mbrace.or.jp/od2/K/202609/k260902.lzh"]
    assert len(harness.commands) == 1
    assert harness.commands[0][1:3] == ["t", "-tLzh"]
    assert not (harness.root / "staging").exists()
    assert list(harness.archive.parent.iterdir()) == [harness.archive]
    history = Path(report.history_dir)
    record = read_json(history / "finished.json")
    assert record["status"] == "downloaded"
    assert record["sha256"] == report.sha256
    assert record["policy"] == {
        "transfer": {
            "timeout_seconds": 30.0,
            "body_deadline_seconds": 120.0,
            "max_bytes": 16777216,
        },
        "max_attempts": 3,
        "retry_delay_seconds": 5.0,
        "archive_timeout_seconds": 30.0,
    }
    assert read_json(history / "attempt-1.json")["http_status"] == 200
    assert read_json(history / "archive_test.json")["exit_code"] == 0
    assert not (harness.root / "manifest/boatrace_official/.download.lock").exists()


def test_verified_cache_needs_no_network_or_seven_zip(harness: Harness) -> None:
    first = harness.run()
    harness.seven_zip.unlink()
    second = harness.run()

    assert second.status is DownloadStatus.SKIPPED_VERIFIED
    assert second.attempts == []
    assert second.acquired_at is None
    assert second.history_dir != first.history_dir
    assert len(harness.urls) == len(harness.commands) == 1
    assert harness.archive.read_bytes() == PAYLOAD


def test_manual_archive_records_observation_without_inventing_acquisition(harness: Harness) -> None:
    harness.archive.parent.mkdir(parents=True)
    harness.archive.write_bytes(PAYLOAD)
    original_mtime = harness.archive.stat().st_mtime_ns

    report = harness.run()

    assert report.status is DownloadStatus.EXISTING_UNTRACKED
    assert report.acquired_at is None and report.attempts == []
    assert harness.urls == []
    assert harness.archive.stat().st_mtime_ns == original_mtime
    assert read_json(Path(report.history_dir) / "verified.json")["source"] == "observed_existing"
    assert harness.run().status is DownloadStatus.SKIPPED_VERIFIED


def test_modified_existing_archive_stops_without_overwriting(harness: Harness) -> None:
    harness.run()
    harness.archive.write_bytes(b"changed")

    report = harness.run()

    assert report.status is DownloadStatus.HISTORY_MISMATCH
    assert not report.archive_verified
    assert harness.archive.read_bytes() == b"changed"
    assert len(harness.urls) == 1


def test_missing_archive_with_verified_history_requires_reconciliation(harness: Harness) -> None:
    harness.run()
    harness.archive.unlink()

    report = harness.run()

    assert report.status is DownloadStatus.HISTORY_MISSING_FILE
    assert not harness.archive.exists()
    assert len(harness.urls) == 1


@pytest.mark.parametrize("status", [403, 404, 429])
def test_permanent_http_errors_are_not_retried(harness: Harness, status: int) -> None:
    harness.replies = [TransferError(f"HTTP {status}", status_code=status)]

    report = harness.run()

    assert report.status is DownloadStatus.FAILED
    assert len(report.attempts) == 1
    assert report.attempts[0].http_status == status
    assert harness.sleeps == []
    assert not harness.archive.exists()
    assert list(harness.archive.parent.iterdir()) == []
    assert read_json(Path(report.history_dir) / "finished.json")["error"] is not None


def test_transient_errors_retry_with_finite_backoff(harness: Harness) -> None:
    harness.replies = [
        TransferError("timeout", retryable=True),
        TransferError("HTTP 503", retryable=True, status_code=503),
        PAYLOAD,
    ]

    report = harness.run()

    assert report.status is DownloadStatus.DOWNLOADED
    assert len(report.attempts) == 3
    assert harness.sleeps == [5.0, 10.0]
    assert harness.archive.read_bytes() == PAYLOAD
    assert report.attempts[0].error == "timeout"


def test_retry_limit_leaves_no_formal_or_partial_archive(harness: Harness) -> None:
    harness.replies = [TransferError("timeout", retryable=True) for _ in range(3)]

    report = harness.run()

    assert report.status is DownloadStatus.FAILED
    assert len(report.attempts) == 3
    assert harness.sleeps == [5.0, 10.0]
    assert list(harness.archive.parent.iterdir()) == []


def test_disk_error_is_not_retried_as_network_failure(harness: Harness) -> None:
    harness.replies = [OSError("disk full")]

    report = harness.run()

    assert report.status is DownloadStatus.FAILED
    assert len(report.attempts) == 1
    assert harness.sleeps == []
    assert not harness.archive.exists()
    assert list(harness.archive.parent.iterdir()) == []


def test_keyboard_interrupt_records_failure_and_cleans_up(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted(url: str, destination: Path, policy: TransferPolicy) -> TransferResult:
        destination.write_bytes(b"partial")
        raise KeyboardInterrupt

    monkeypatch.setattr(downloader, "fetch_archive_once", interrupted)
    with pytest.raises(KeyboardInterrupt):
        harness.run()

    records = list((harness.root / "manifest").rglob("finished.json"))
    assert len(records) == 1
    assert read_json(records[0])["status"] == "failed"
    assert "KeyboardInterrupt" in str(read_json(records[0])["error"])
    assert not (harness.root / "manifest/boatrace_official/.download.lock").exists()
    assert list(harness.archive.parent.iterdir()) == []


@pytest.mark.parametrize("exit_code", [1, 2])
def test_archive_validation_warning_or_error_prevents_publication(
    harness: Harness,
    exit_code: int,
) -> None:
    harness.validator_exit = exit_code

    report = harness.run()

    assert report.status is DownloadStatus.FAILED
    assert not report.archive_verified
    assert not harness.archive.exists()
    assert len(harness.urls) == 1 and harness.sleeps == []
    history = Path(report.history_dir)
    assert read_json(history / "archive_test.json")["exit_code"] == exit_code
    assert not (history / "verified.json").exists()


def test_archive_timeout_is_recorded_and_not_retried(harness: Harness) -> None:
    harness.validator_timeout = True

    report = harness.run()

    assert report.status is DownloadStatus.FAILED
    assert not harness.archive.exists()
    record = read_json(Path(report.history_dir) / "archive_test.json")
    assert record["timed_out"] is True
    assert record["stdout"] == "partial output"
    assert len(harness.urls) == 1


def test_bad_manual_archive_is_preserved_and_not_fetched(harness: Harness) -> None:
    harness.archive.parent.mkdir(parents=True)
    harness.archive.write_bytes(b"damaged original")
    harness.validator_exit = 2

    report = harness.run()

    assert report.status is DownloadStatus.FAILED
    assert harness.archive.read_bytes() == b"damaged original"
    assert harness.urls == []


def test_missing_seven_zip_prevents_network(harness: Harness) -> None:
    harness.seven_zip.unlink()

    report = harness.run()

    assert report.status is DownloadStatus.FAILED
    assert harness.urls == []


def test_busy_lock_is_not_removed_or_waited_on(harness: Harness) -> None:
    lock = harness.root / "manifest/boatrace_official/.download.lock"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text("other process", encoding="utf-8")

    report = harness.run()

    assert report.status is DownloadStatus.BUSY
    assert (lock / "owner.json").read_text(encoding="utf-8") == "other process"
    assert harness.urls == [] and harness.sleeps == []


def test_publication_race_preserves_competitors_file(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_link = os.link

    def competing_link(source: Path, destination: Path) -> None:
        if destination == harness.archive:
            destination.write_bytes(b"competitor")
        original_link(source, destination)

    monkeypatch.setattr(downloader.os, "link", competing_link)

    report = harness.run()

    assert report.status is DownloadStatus.FAILED
    assert harness.archive.read_bytes() == b"competitor"
    assert report.acquired_at is None
    assert list(harness.archive.parent.iterdir()) == [harness.archive]


def test_history_start_failure_prevents_network(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(path: Path, payload: object) -> None:
        raise HistoryWriteError("disk full")

    monkeypatch.setattr(downloader, "_write_json", fail_write)

    with pytest.raises(HistoryWriteError):
        harness.run()
    assert harness.urls == []
    assert not harness.archive.exists()


def test_verified_history_failure_prevents_publication(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_link = os.link

    def fail_verified_write(source: Path, destination: Path) -> None:
        if destination.name == "verified.json":
            raise OSError("disk full")
        original_link(source, destination)

    monkeypatch.setattr(downloader.os, "link", fail_verified_write)

    report = harness.run()

    assert report.status is DownloadStatus.FAILED
    assert not harness.archive.exists()
    assert not (Path(report.history_dir) / "verified.json").exists()


def test_final_history_failure_leaves_reconcilable_file_and_releases_lock(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_link = os.link

    def fail_finished_write(source: Path, destination: Path) -> None:
        if destination.name == "finished.json":
            raise OSError("disk full")
        original_link(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(downloader.os, "link", fail_finished_write)
        with pytest.raises(HistoryWriteError):
            harness.run()
    assert harness.archive.read_bytes() == PAYLOAD
    assert not (harness.root / "manifest/boatrace_official/.download.lock").exists()
    assert harness.run().status is DownloadStatus.SKIPPED_VERIFIED
    assert len(harness.urls) == 1


def test_corrupt_history_stops_without_network(harness: Harness) -> None:
    first = harness.run()
    (Path(first.history_dir) / "verified.json").write_text("{incomplete", encoding="utf-8")

    report = harness.run()

    assert report.status is DownloadStatus.FAILED
    assert harness.archive.read_bytes() == PAYLOAD
    assert len(harness.urls) == 1


def test_unavailable_hard_links_never_fall_back_to_overwrite(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_link(source: Path, destination: Path) -> None:
        raise OSError("hard links unsupported")

    monkeypatch.setattr(downloader.os, "link", fail_link)

    with pytest.raises(HistoryWriteError):
        harness.run()
    assert harness.urls == []


@pytest.mark.parametrize("count", [0, 4, True])
def test_invalid_retry_count_is_rejected(count: int) -> None:
    with pytest.raises(ValueError):
        DownloadPolicy(max_attempts=count)


@pytest.mark.parametrize("delay", [0.0, float("inf"), float("nan")])
def test_invalid_retry_delay_is_rejected(delay: float) -> None:
    with pytest.raises(ValueError):
        DownloadPolicy(retry_delay_seconds=delay)
