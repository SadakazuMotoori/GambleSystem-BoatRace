"""公式B/Kの少量・手動起動向け取得、整合性検査、追記型の履歴保存。"""

import hashlib
import json
import math
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from uuid import uuid4

from boatrace.official_files import OfficialDataKind, OfficialFileSpec, build_official_file_spec
from boatrace.official_http import TransferError, TransferPolicy, TransferResult, fetch_archive_once


class DownloadStatus(StrEnum):
    STARTED = "started"
    DOWNLOADED = "downloaded"
    SKIPPED_VERIFIED = "skipped_verified"
    EXISTING_UNTRACKED = "existing_untracked"
    HISTORY_MISMATCH = "history_mismatch"
    HISTORY_MISSING_FILE = "history_missing_file"
    BUSY = "busy"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DownloadPolicy:
    transfer: TransferPolicy = field(default_factory=TransferPolicy)
    max_attempts: int = 3
    retry_delay_seconds: float = 5.0
    archive_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 3:
            raise ValueError("試行回数は1〜3回で指定する。")
        if not math.isfinite(self.retry_delay_seconds) or self.retry_delay_seconds < 1:
            raise ValueError("再試行の待機秒数は有限の1以上で指定する。")
        if not math.isfinite(self.archive_timeout_seconds) or self.archive_timeout_seconds <= 0:
            raise ValueError("アーカイブ検査の制限秒数は有限の正数で指定する。")


@dataclass(slots=True)
class AttemptRecord:
    number: int
    started_at: str
    finished_at: str | None = None
    http_status: int | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    error: str | None = None


@dataclass(slots=True)
class DownloadReport:
    run_id: str
    kind: str
    target_date: str
    url: str
    archive_path: str
    history_dir: str
    started_at: str
    program_version: str
    implementation_sha256: str
    policy: DownloadPolicy
    seven_zip_path: str
    schema_version: int = 1
    status: DownloadStatus = DownloadStatus.STARTED
    finished_at: str | None = None
    acquired_at: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    archive_verified: bool = False
    error: str | None = None
    attempts: list[AttemptRecord] = field(default_factory=list[AttemptRecord])
    cleanup_errors: list[str] = field(default_factory=list[str])


class HistoryWriteError(RuntimeError):
    """履歴保存失敗。正式ファイルが存在し得るため、自動的に再取得しない。"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: object) -> None:
    """履歴を置換せず、完全に書いた一時ファイルから公開する。"""
    try:
        with TemporaryDirectory(prefix=".history-", dir=path.parent) as working:
            temporary = Path(working) / "record.json"
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
    except OSError as exc:
        raise HistoryWriteError(f"履歴を書き込めない: {path}: {exc}") from exc


def _fingerprint(path: Path, max_bytes: int) -> tuple[int, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"保存先が通常ファイルではない: {path}")
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("既存ファイルがサイズ上限を超えている。")
            digest.update(chunk)
    if size == 0:
        raise ValueError("既存ファイルが空。")
    return size, digest.hexdigest()


def _baseline(history_root: Path, spec: OfficialFileSpec) -> tuple[int, str] | None:
    """過去の検査済みバイトを照合基準とする。取得成功とは区別する。"""
    fingerprints: set[tuple[int, str]] = set()
    for path in history_root.glob("*/verified.json"):
        if path.stat().st_size > 64 * 1024:
            raise ValueError(f"検査履歴が大きすぎる: {path}")
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"検査履歴の形式が不正: {path}")
        record = cast(dict[str, object], raw)
        size = record.get("size_bytes")
        digest = record.get("sha256")
        if (
            record.get("schema_version") != 1
            or record.get("url") != spec.url
            or record.get("target_date") != spec.target_date.isoformat()
            or record.get("kind") != spec.kind.value
            or type(size) is not int
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(f"検査履歴の形式・対象が不正: {path}")
        if size <= 0:
            raise ValueError(f"検査履歴のサイズが不正: {path}")
        fingerprints.add((size, digest))
    if len(fingerprints) > 1:
        raise ValueError("過去の検査履歴同士でハッシュ・サイズが食い違っている。")
    return next(iter(fingerprints), None)


def _validate_archive(
    path: Path, seven_zip_path: Path, timeout_seconds: float, history_dir: Path
) -> None:
    """形式をLZHに固定して検査する。内部名・展開サイズの検査は展開工程で行う。"""
    command = [str(seven_zip_path), "t", "-tLzh", "-bd", "-bb0", "-sccUTF-8", str(path)]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _write_json(
            history_dir / "archive_test.json",
            {
                "command": command,
                "timed_out": True,
                "stdout": (exc.stdout or b"").decode("utf-8", errors="replace"),
                "stderr": (exc.stderr or b"").decode("utf-8", errors="replace"),
            },
        )
        raise ValueError("7-Zip検査が制限時間を超えた。") from exc
    _write_json(
        history_dir / "archive_test.json",
        {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout.decode("utf-8", errors="replace"),
            "stderr": result.stderr.decode("utf-8", errors="replace"),
        },
    )
    if result.returncode != 0:
        raise ValueError(f"7-ZipのLZH検査が失敗した。終了コード: {result.returncode}")


def _save_verified(report: DownloadReport, source: str) -> None:
    _write_json(
        Path(report.history_dir) / "verified.json",
        {
            "schema_version": 1,
            "kind": report.kind,
            "target_date": report.target_date,
            "url": report.url,
            "archive_path": report.archive_path,
            "size_bytes": report.size_bytes,
            "sha256": report.sha256,
            "verified_at": _now(),
            "source": source,
        },
    )


def _require_seven_zip(path: Path) -> None:
    if not path.is_absolute() or not path.is_file():
        raise ValueError("7-Zip実行ファイルの絶対パスが必要だ。")


def _download_locked(
    spec: OfficialFileSpec, history_root: Path, report: DownloadReport, seven_zip_path: Path
) -> None:
    policy = report.policy
    archive_path = spec.archive_path
    baseline = _baseline(history_root, spec)
    if archive_path.exists() or archive_path.is_symlink():
        report.size_bytes, report.sha256 = _fingerprint(archive_path, policy.transfer.max_bytes)
        if baseline is not None:
            if baseline != (report.size_bytes, report.sha256):
                report.status = DownloadStatus.HISTORY_MISMATCH
                report.error = "既存ファイルが検査履歴と一致しない。再取得せず調査が必要だ。"
            else:
                report.status = DownloadStatus.SKIPPED_VERIFIED
                report.archive_verified = True
            return
        _require_seven_zip(seven_zip_path)
        _validate_archive(
            archive_path, seven_zip_path, policy.archive_timeout_seconds, Path(report.history_dir)
        )
        if _fingerprint(archive_path, policy.transfer.max_bytes) != (
            report.size_bytes,
            report.sha256,
        ):
            raise ValueError("検査中に既存ファイルが変化した。")
        report.archive_verified = True
        _save_verified(report, "observed_existing")
        report.status = DownloadStatus.EXISTING_UNTRACKED
        return
    if baseline is not None:
        report.status = DownloadStatus.HISTORY_MISSING_FILE
        report.error = "検査履歴はあるが正式ファイルがない。再取得せず調査が必要だ。"
        return

    _require_seven_zip(seven_zip_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{archive_path.stem}-", dir=archive_path.parent) as working:
        for number in range(1, policy.max_attempts + 1):
            temporary = Path(working) / f"attempt-{number}.lzh"
            attempt = AttemptRecord(number=number, started_at=_now())
            report.attempts.append(attempt)
            transfer: TransferResult | None = None
            try:
                transfer = fetch_archive_once(spec.url, temporary, policy.transfer)
                attempt.http_status = transfer.status_code
                attempt.size_bytes = transfer.size_bytes
                attempt.sha256 = transfer.sha256
            except TransferError as exc:
                attempt.http_status = exc.status_code
                attempt.error = str(exc)
                if not exc.retryable or number == policy.max_attempts:
                    raise
            except OSError as exc:
                attempt.error = f"ファイル操作失敗: {exc}"
                raise
            finally:
                attempt.finished_at = _now()
                _write_json(Path(report.history_dir) / f"attempt-{number}.json", asdict(attempt))
            if transfer is None:
                temporary.unlink(missing_ok=True)
                time.sleep(policy.retry_delay_seconds * number)
                continue

            report.size_bytes = transfer.size_bytes
            report.sha256 = transfer.sha256
            _validate_archive(
                temporary, seven_zip_path, policy.archive_timeout_seconds, Path(report.history_dir)
            )
            if _fingerprint(temporary, policy.transfer.max_bytes) != (
                report.size_bytes,
                report.sha256,
            ):
                raise ValueError("取得後の一時ファイルが変化した。")
            report.archive_verified = True
            _save_verified(report, "validated_download_before_publish")
            # 同一ボリュームのハードリンクで確定する。存在確認後の競合でも置換しない。
            # 対応しないファイルシステムでは失敗させ、上書き可能な方式へ切り替えない。
            os.link(temporary, archive_path)
            report.acquired_at = _now()
            report.status = DownloadStatus.DOWNLOADED
            return


def download_official_file(
    data_root: Path,
    target_date: date,
    kind: OfficialDataKind,
    seven_zip_path: Path,
    *,
    policy: DownloadPolicy | None = None,
) -> DownloadReport:
    """指定した1日・1種別だけ取得する。展開・CLI設定読込・定期実行は行わない。

    rawとmanifestはハードリンクを利用できるローカルFSを使う。
    異常終了後の残存ロックは自動削除せず、確認して解除する。
    履歴の開始・終了を書けなければHistoryWriteErrorを送出する。
    通常の取得失敗はreport.status/errorで返すので、呼出側は必ず確認する。
    """
    spec = build_official_file_spec(data_root, target_date, kind)
    chosen_policy = policy if policy is not None else DownloadPolicy()
    source_hash = hashlib.sha256()
    for name in ("official_download.py", "official_http.py", "official_files.py"):
        source_hash.update(Path(__file__).with_name(name).read_bytes())
    try:
        program_version = version("boatrace")
    except PackageNotFoundError:
        program_version = "uninstalled"

    manifest_root = data_root / "manifest" / "boatrace_official"
    history_root = (
        manifest_root
        / kind.value
        / f"{target_date.year:04d}"
        / f"{target_date.month:02d}"
        / spec.archive_path.stem
    )
    run_id = uuid4().hex
    history_dir = history_root / run_id
    report = DownloadReport(
        run_id=run_id,
        kind=kind.value,
        target_date=target_date.isoformat(),
        url=spec.url,
        archive_path=str(spec.archive_path),
        history_dir=str(history_dir),
        started_at=_now(),
        program_version=program_version,
        implementation_sha256=source_hash.hexdigest(),
        policy=chosen_policy,
        seven_zip_path=str(seven_zip_path),
    )
    try:
        history_dir.mkdir(parents=True)
    except OSError as exc:
        raise HistoryWriteError(f"履歴ディレクトリを作成できない: {history_dir}") from exc
    _write_json(history_dir / "started.json", asdict(report))
    lock = manifest_root / ".download.lock"
    locked = False
    try:
        try:
            lock.mkdir()
        except FileExistsError:
            report.status = DownloadStatus.BUSY
            report.error = f"取得ロックが存在する。実行中か残存ロックかを確認する: {lock}"
        else:
            locked = True
            _write_json(
                lock / "owner.json",
                {
                    "run_id": run_id,
                    "pid": os.getpid(),
                    "started_at": report.started_at,
                    "history_dir": str(history_dir),
                },
            )
            _download_locked(spec, history_root, report, seven_zip_path)
    except (OSError, ValueError, TransferError, HistoryWriteError) as exc:
        report.status = DownloadStatus.FAILED
        report.error = f"{type(exc).__name__}: {exc}"
    except KeyboardInterrupt:
        report.status = DownloadStatus.FAILED
        report.error = "KeyboardInterrupt: 利用者が処理を中断した。"
        raise
    finally:
        report.finished_at = _now()
        # 履歴確定までロックを保ち、履歴保存が失敗しても自分のロックは解放する。
        try:
            _write_json(history_dir / "finished.json", asdict(report))
        finally:
            if locked:
                try:
                    (lock / "owner.json").unlink(missing_ok=True)
                    lock.rmdir()
                except OSError as exc:
                    report.cleanup_errors.append(str(exc))
                    _write_json(
                        history_dir / "cleanup_error.json",
                        {"errors": report.cleanup_errors, "lock": str(lock)},
                    )
    return report
