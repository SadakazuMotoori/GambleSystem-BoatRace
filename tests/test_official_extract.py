"""公式展開の原本保持・既存照合・不正な内部一覧・プロセス制限を検証する。"""

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import cast

import pytest

import boatrace.official_extract as extractor
from boatrace.official_extract import (
    ExtractHistoryError,
    ExtractPolicy,
    ExtractReport,
    ExtractStatus,
    extract_official_file,
    parse_lzh_listing,
    validate_official_text,
)
from boatrace.official_files import OfficialDataKind, OfficialFileSpec, build_official_file_spec

SAMPLE_DATE = date(2026, 9, 2)
ARCHIVE = b"placeholder archive; archive calls are replaced in orchestration tests"
TEXT = "STARTK\r\n13KBGN\r\n尼　崎\r\n13KEND\r\nFINALK\r\n".encode("cp932")


def make_listing(name: str = "K260902.TXT", size: int = len(TEXT)) -> str:
    return (
        f"Path = {name}\nFolder = -\nSize = {size}\nPacked Size = 30\n"
        "Modified = 2026-09-02 22:49:40\nCRC = 00003A0E\nMethod = -lh5-\nHost OS = MS-DOS\n"
    )


def read_json(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


@dataclass
class Harness:
    root: Path
    seven_zip: Path
    listing: str = field(default_factory=make_listing)
    text: bytes = TEXT
    fail_extract: bool = False
    calls: list[list[str]] = field(default_factory=list[list[str]])

    @property
    def spec(self) -> OfficialFileSpec:
        return build_official_file_spec(self.root, SAMPLE_DATE, OfficialDataKind.RESULT)

    def run(self) -> ExtractReport:
        return extract_official_file(
            self.root, SAMPLE_DATE, OfficialDataKind.RESULT, self.seven_zip
        )

    def command(
        self,
        command: list[str],
        destination: Path,
        max_bytes: int,
        timeout: float,
        record_path: Path,
    ) -> None:
        self.calls.append(command)
        assert "-tLzh" in command
        assert timeout == 30.0
        if command[1] == "l":
            assert "-slt" in command and "-ba" in command
            destination.write_bytes(self.listing.encode("utf-8"))
        else:
            assert command[1] == "e" and "-so" in command
            assert command[-1] == "K260902.TXT"
            assert Path(command[-2]).read_bytes() == ARCHIVE
            destination.write_bytes(self.text)
            if self.fail_extract:
                raise ValueError("7-Zip: CRC Error")
        record_path.write_text('{"exit_code": 0}', encoding="utf-8")


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    seven_zip = tmp_path / "7z.exe"
    seven_zip.write_bytes(b"placeholder")
    result = Harness(tmp_path / "BoatRaceData", seven_zip)
    result.spec.archive_path.parent.mkdir(parents=True)
    result.spec.archive_path.write_bytes(ARCHIVE)
    monkeypatch.setattr(extractor, "run_archive_command", result.command)
    return result


def test_new_extraction_preserves_bytes_and_records_provenance(harness: Harness) -> None:
    report = harness.run()

    assert report.status is ExtractStatus.EXTRACTED
    assert report.text_verified and report.extracted_at is not None
    assert report.text_size_bytes == len(TEXT)
    assert report.text_sha256 == hashlib.sha256(TEXT).hexdigest()
    assert report.archive_sha256 == hashlib.sha256(ARCHIVE).hexdigest()
    assert harness.spec.text_path.read_bytes() == TEXT
    assert harness.spec.archive_path.read_bytes() == ARCHIVE
    assert len(harness.calls) == 2
    assert list(harness.spec.staging_dir.parent.iterdir()) == [harness.spec.staging_dir]
    history = Path(report.history_dir)
    assert read_json(history / "verified.json")["encoding"] == "cp932"
    assert read_json(history / "finished.json")["status"] == "extracted"
    assert len(report.implementation_sha256) == 64
    assert not (harness.root / "manifest/boatrace_official/.extract.lock").exists()


def test_cache_checks_both_original_and_text_without_seven_zip(harness: Harness) -> None:
    first = harness.run()
    harness.seven_zip.unlink()

    report = harness.run()

    assert report.status is ExtractStatus.SKIPPED_VERIFIED
    assert report.extracted_at is None and report.text_verified
    assert first.history_dir != report.history_dir
    assert len(harness.calls) == 2


def test_manual_text_is_compared_with_reproduced_archive_bytes(harness: Harness) -> None:
    harness.spec.staging_dir.mkdir(parents=True)
    harness.spec.text_path.write_bytes(TEXT)
    original_mtime = harness.spec.text_path.stat().st_mtime_ns

    report = harness.run()

    assert report.status is ExtractStatus.EXISTING_UNTRACKED
    assert report.extracted_at is None
    assert len(harness.calls) == 2
    assert harness.spec.text_path.stat().st_mtime_ns == original_mtime
    assert read_json(Path(report.history_dir) / "verified.json")["source"] == "observed_existing"
    assert harness.run().status is ExtractStatus.SKIPPED_VERIFIED


def test_untracked_wrong_text_is_preserved(harness: Harness) -> None:
    harness.spec.staging_dir.mkdir(parents=True)
    harness.spec.text_path.write_bytes(b"wrong manual text")

    report = harness.run()

    assert report.status is ExtractStatus.TEXT_MISMATCH
    assert harness.spec.text_path.read_bytes() == b"wrong manual text"
    assert not (Path(report.history_dir) / "verified.json").exists()


@pytest.mark.parametrize("which", ["archive", "text"])
def test_changed_verified_file_stops_before_seven_zip(harness: Harness, which: str) -> None:
    harness.run()
    path = harness.spec.archive_path if which == "archive" else harness.spec.text_path
    path.write_bytes(b"changed")

    report = harness.run()

    expected = ExtractStatus.ARCHIVE_MISMATCH if which == "archive" else ExtractStatus.TEXT_MISMATCH
    assert report.status is expected
    assert path.read_bytes() == b"changed"
    assert len(harness.calls) == 2


def test_missing_staging_with_history_is_not_recreated(harness: Harness) -> None:
    harness.run()
    harness.spec.text_path.unlink()
    harness.spec.staging_dir.rmdir()

    report = harness.run()

    assert report.status is ExtractStatus.HISTORY_MISSING_FILE
    assert not harness.spec.staging_dir.exists()
    assert len(harness.calls) == 2


@pytest.mark.parametrize("extra_file", [False, True])
def test_incomplete_or_extra_staging_is_preserved(harness: Harness, extra_file: bool) -> None:
    harness.spec.staging_dir.mkdir(parents=True)
    if extra_file:
        harness.spec.text_path.write_bytes(TEXT)
        (harness.spec.staging_dir / "extra.txt").write_bytes(b"keep")

    report = harness.run()

    assert report.status is ExtractStatus.FAILED
    assert harness.spec.staging_dir.is_dir()
    assert harness.calls == []
    if extra_file:
        assert (harness.spec.staging_dir / "extra.txt").read_bytes() == b"keep"


@pytest.mark.parametrize("matching", [False, True])
def test_download_receipt_is_checked(harness: Harness, matching: bool) -> None:
    history = harness.root / "manifest/boatrace_official/result/2026/09/k260902/manual"
    history.mkdir(parents=True)
    (history / "verified.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "result",
                "target_date": "2026-09-02",
                "url": harness.spec.url,
                "size_bytes": len(ARCHIVE),
                "sha256": hashlib.sha256(ARCHIVE).hexdigest() if matching else "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    report = harness.run()

    assert report.status is (
        ExtractStatus.EXTRACTED if matching else ExtractStatus.ARCHIVE_MISMATCH
    )
    assert report.download_history_found
    assert len(harness.calls) == (2 if matching else 0)


def test_original_change_during_extraction_prevents_publication(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def change_archive(
        command: list[str],
        destination: Path,
        max_bytes: int,
        timeout: float,
        record_path: Path,
    ) -> None:
        harness.command(command, destination, max_bytes, timeout, record_path)
        if command[1] == "e":
            harness.spec.archive_path.write_bytes(b"changed by another process")

    monkeypatch.setattr(extractor, "run_archive_command", change_archive)

    report = harness.run()

    assert report.status is ExtractStatus.FAILED
    assert not harness.spec.text_path.exists()
    assert harness.spec.archive_path.read_bytes() == b"changed by another process"


def test_missing_original_fails_without_creating_staging(harness: Harness) -> None:
    harness.spec.archive_path.unlink()

    report = harness.run()

    assert report.status is ExtractStatus.FAILED
    assert harness.calls == []
    assert not harness.spec.staging_dir.exists()


def test_invalid_internal_name_stops_before_extraction(harness: Harness) -> None:
    harness.listing = make_listing("../K260902.TXT")

    report = harness.run()

    assert report.status is ExtractStatus.FAILED
    assert len(harness.calls) == 1
    assert not harness.spec.staging_dir.exists()
    assert list(harness.spec.staging_dir.parent.iterdir()) == []


@pytest.mark.parametrize("bad_text", [b"STARTK\n\x81", b"not a result", TEXT + b"extra"])
def test_invalid_extracted_text_is_not_published(harness: Harness, bad_text: bytes) -> None:
    harness.text = bad_text
    harness.listing = make_listing(size=len(bad_text))

    report = harness.run()

    assert report.status is ExtractStatus.FAILED
    assert not harness.spec.text_path.exists()
    assert harness.spec.archive_path.read_bytes() == ARCHIVE


def test_size_mismatch_is_not_published(harness: Harness) -> None:
    harness.listing = make_listing(size=len(TEXT) + 1)

    assert harness.run().status is ExtractStatus.FAILED
    assert not harness.spec.staging_dir.exists()


def test_crc_failure_is_not_published(harness: Harness) -> None:
    harness.fail_extract = True

    report = harness.run()

    assert report.status is ExtractStatus.FAILED
    assert not report.text_verified
    assert not harness.spec.staging_dir.exists()
    assert list(harness.spec.staging_dir.parent.iterdir()) == []


def test_busy_lock_is_left_intact(harness: Harness) -> None:
    lock = harness.root / "manifest/boatrace_official/.extract.lock"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text("other process", encoding="utf-8")

    report = harness.run()

    assert report.status is ExtractStatus.BUSY
    assert harness.calls == []
    assert (lock / "owner.json").read_text(encoding="utf-8") == "other process"


def test_publication_race_does_not_replace_file(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_link = os.link

    def competing_link(source: Path, destination: Path) -> None:
        if destination == harness.spec.text_path:
            destination.write_bytes(b"competitor")
        original_link(source, destination)

    monkeypatch.setattr(extractor.os, "link", competing_link)
    report = harness.run()

    assert report.status is ExtractStatus.FAILED
    assert harness.spec.text_path.read_bytes() == b"competitor"
    assert report.extracted_at is None


@pytest.mark.parametrize("phase", ["started.json", "verified.json", "finished.json"])
def test_history_failure_does_not_claim_success(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    original_link = os.link

    def fail_link(source: Path, destination: Path) -> None:
        if destination.name == phase:
            raise OSError("disk full")
        original_link(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(extractor.os, "link", fail_link)
        if phase == "verified.json":
            assert harness.run().status is ExtractStatus.FAILED
        else:
            with pytest.raises(ExtractHistoryError):
                harness.run()
    assert not (harness.root / "manifest/boatrace_official/.extract.lock").exists()
    if phase == "finished.json":
        assert harness.spec.text_path.read_bytes() == TEXT
        assert harness.run().status is ExtractStatus.SKIPPED_VERIFIED
    else:
        assert not harness.spec.staging_dir.exists()
    if phase == "started.json":
        assert harness.calls == []


def test_corrupt_receipt_is_not_ignored(harness: Harness) -> None:
    first = harness.run()
    (Path(first.history_dir) / "verified.json").write_text("{incomplete", encoding="utf-8")

    assert harness.run().status is ExtractStatus.FAILED
    assert len(harness.calls) == 2
    assert harness.spec.text_path.read_bytes() == TEXT


@pytest.mark.parametrize(
    "name",
    [
        "../K260902.TXT",
        "..\\K260902.TXT",
        "/K260902.TXT",
        "C:\\K260902.TXT",
        "folder/K260902.TXT",
        "K260902.TXT:stream",
        "K260903.TXT",
        "B260902.TXT",
        "k260902.txt",
    ],
)
def test_listing_rejects_unsafe_or_unexpected_names(name: str) -> None:
    with pytest.raises(ValueError):
        parse_lzh_listing(make_listing(name), "K260902.TXT", 1024)


@pytest.mark.parametrize(
    "listing",
    [
        "",
        make_listing() + make_listing(),
        make_listing().replace("Folder = -", "Folder = +"),
        make_listing().replace("Method = -lh5-", "Method = -lhd-"),
        make_listing().replace("CRC = 00003A0E", "CRC = "),
        make_listing() + "Symbolic Link = ../other\n",
        make_listing() + "Attributes = A_ lrwxrwxrwx\n",
        make_listing() + "Attributes = D\n",
        make_listing(size=0),
        make_listing(size=1025),
    ],
)
def test_listing_rejects_multiple_members_links_and_invalid_sizes(listing: str) -> None:
    with pytest.raises(ValueError):
        parse_lzh_listing(listing, "K260902.TXT", 1024)


@pytest.mark.parametrize(
    "kind, name, body",
    [
        (OfficialDataKind.RESULT, "K260902.TXT", TEXT),
        (OfficialDataKind.PROGRAM, "B260902.TXT", b"STARTB\r\n24BBGN\r\n24BEND\r\nFINALB\r\n"),
    ],
)
def test_supported_b_and_k_envelopes(kind: OfficialDataKind, name: str, body: bytes) -> None:
    assert parse_lzh_listing(make_listing(name, len(body)), name, 1024) == len(body)
    validate_official_text(body, kind)


@pytest.mark.parametrize("value", [0, -1, True])
def test_invalid_size_policy(value: int) -> None:
    with pytest.raises(ValueError):
        ExtractPolicy(max_text_bytes=value)


def test_process_runner_captures_exact_binary_output(tmp_path: Path) -> None:
    output = tmp_path / "output"
    record = tmp_path / "command.json"
    extractor.run_archive_command(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([0, 255, 13, 10]))"],
        output,
        4,
        10.0,
        record,
    )
    assert output.read_bytes() == b"\x00\xff\r\n"
    assert read_json(record)["exit_code"] == 0


@pytest.mark.parametrize(
    "script, limit, timeout, expected",
    [
        ("import sys; sys.stdout.buffer.write(b'x' * 1000000)", 100, 10.0, "サイズ上限"),
        ("import sys; sys.stderr.write('CRC Error'); sys.exit(2)", 100, 10.0, "終了コード: 2"),
        ("import time; time.sleep(30)", 100, 0.1, "制限時間"),
    ],
)
def test_process_runner_stops_failed_oversized_or_hung_process(
    tmp_path: Path,
    script: str,
    limit: int,
    timeout: float,
    expected: str,
) -> None:
    output = tmp_path / "output"
    record = tmp_path / "command.json"
    with pytest.raises(ValueError, match=expected):
        extractor.run_archive_command(
            [sys.executable, "-c", script],
            output,
            limit,
            timeout,
            record,
        )
    assert output.stat().st_size <= limit
    assert read_json(record)["error"] is not None
