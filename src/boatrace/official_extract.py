"""公式LZHから検査済みのB/Kテキストを再現し、上書きせず保存する。"""

import hashlib
import json
import math
import os
import re
import subprocess
import threading
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from io import BufferedReader
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from typing import cast
from uuid import uuid4

from boatrace.official_files import OfficialDataKind, OfficialFileSpec, build_official_file_spec


class ExtractStatus(StrEnum):
    STARTED = "started"
    EXTRACTED = "extracted"
    EXISTING_UNTRACKED = "existing_untracked"
    SKIPPED_VERIFIED = "skipped_verified"
    ARCHIVE_MISMATCH = "archive_mismatch"
    TEXT_MISMATCH = "text_mismatch"
    HISTORY_MISSING_FILE = "history_missing_file"
    BUSY = "busy"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExtractPolicy:
    max_archive_bytes: int = 16 * 1024 * 1024
    max_text_bytes: int = 32 * 1024 * 1024
    command_timeout_seconds: float = 30.0
    max_listing_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        for value in (self.max_archive_bytes, self.max_text_bytes, self.max_listing_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("サイズ上限は正の整数で指定する。")
        if not math.isfinite(self.command_timeout_seconds) or self.command_timeout_seconds <= 0:
            raise ValueError("検査・展開の制限秒数は有限の正数で指定する。")


@dataclass(slots=True)
class ExtractReport:
    run_id: str
    kind: str
    target_date: str
    archive_path: str
    text_path: str
    history_dir: str
    started_at: str
    program_version: str
    implementation_sha256: str
    policy: ExtractPolicy
    seven_zip_path: str
    schema_version: int = 1
    status: ExtractStatus = ExtractStatus.STARTED
    finished_at: str | None = None
    extracted_at: str | None = None
    archive_size_bytes: int | None = None
    archive_sha256: str | None = None
    text_size_bytes: int | None = None
    text_sha256: str | None = None
    download_history_found: bool = False
    text_verified: bool = False
    error: str | None = None
    cleanup_errors: list[str] = field(default_factory=list[str])


class ExtractHistoryError(RuntimeError):
    """展開履歴を保存できない。現物確認なしで再展開しない。"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: object) -> None:
    try:
        with TemporaryDirectory(prefix=".record-", dir=path.parent) as work:
            temporary = Path(work) / "record.json"
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
    except OSError as exc:
        raise ExtractHistoryError(f"展開履歴を書き込めない: {path}: {exc}") from exc


def _read_bytes(path: Path, limit: int) -> bytes:
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError(f"通常ファイルではない: {path}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if not 0 < len(data) <= limit:
        raise ValueError(f"ファイルのサイズが制限外: {path}")
    return data


def _fingerprint(data: bytes) -> tuple[int, str]:
    return len(data), hashlib.sha256(data).hexdigest()


def _read_receipt(path: Path, spec: OfficialFileSpec) -> dict[str, object]:
    raw: object = json.loads(_read_bytes(path, 64 * 1024).decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"履歴の形式が不正: {path}")
    record = cast(dict[str, object], raw)
    if (
        record.get("schema_version") != 1
        or record.get("kind") != spec.kind.value
        or record.get("target_date") != spec.target_date.isoformat()
    ):
        raise ValueError(f"履歴の形式・対象が不正: {path}")
    return record


def _receipt_hash(record: dict[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError(f"履歴のハッシュが不正: {key}")
    return value


def _receipt_size(record: dict[str, object], key: str) -> int:
    value = record.get(key)
    if type(value) is not int or value <= 0:
        raise ValueError(f"履歴のサイズが不正: {key}")
    return value


def _download_baseline(root: Path, spec: OfficialFileSpec) -> tuple[int, str] | None:
    entries: set[tuple[int, str]] = set()
    for path in root.glob("*/verified.json"):
        record = _read_receipt(path, spec)
        if record.get("url") != spec.url:
            raise ValueError("取得履歴のURLが対象と一致しない。")
        entries.add((_receipt_size(record, "size_bytes"), _receipt_hash(record, "sha256")))
    if len(entries) > 1:
        raise ValueError("取得履歴同士が食い違っている。")
    return next(iter(entries), None)


def _extract_baseline(root: Path, spec: OfficialFileSpec) -> tuple[str, int, str] | None:
    entries: set[tuple[str, int, str]] = set()
    for path in root.glob("*/verified.json"):
        record = _read_receipt(path, spec)
        if record.get("text_name") != spec.text_path.name or record.get("encoding") != "cp932":
            raise ValueError("展開履歴の内部名・文字コードが不正。")
        entries.add(
            (
                _receipt_hash(record, "archive_sha256"),
                _receipt_size(record, "text_size_bytes"),
                _receipt_hash(record, "text_sha256"),
            )
        )
    if len(entries) > 1:
        raise ValueError("展開履歴同士が食い違っている。")
    return next(iter(entries), None)


def parse_lzh_listing(listing: str, expected_name: str, max_text_bytes: int) -> int:
    """7z l -slt -baの出力から、想定名の通常ファイル1件だけを受け入れる。"""
    fields: dict[str, str] = {}
    allowed = {
        "Path",
        "Folder",
        "Size",
        "Packed Size",
        "Modified",
        "CRC",
        "Method",
        "Host OS",
        "Attributes",
    }
    for line in listing.splitlines():
        if not line:
            continue
        key, separator, value = line.partition(" = ")
        if not separator or key not in allowed or key in fields:
            raise ValueError("内部一覧に未対応項目・重複項目・複数ファイルがある。")
        fields[key] = value
    if fields.get("Path") != expected_name or fields.get("Folder") != "-":
        raise ValueError("内部名が想定TXTと一致しない、または通常ファイルではない。")
    if re.fullmatch(r"[.RASH_ ]*(?:-[rwxstST-]{9})?", fields.get("Attributes", "")) is None:
        raise ValueError("通常ファイル以外、または未対応の属性がある。")
    # 現物で観測したlh5と、無圧縮のlh0を初期対応範囲とする。
    if fields.get("Method") not in {"-lh0-", "-lh5-"}:
        raise ValueError("未対応のLZH圧縮方式。")
    if re.fullmatch("[0-9A-Fa-f]{8}", fields.get("CRC", "")) is None:
        raise ValueError("LZHのCRC情報がない、または不正。")
    for key in ("Size", "Packed Size"):
        if re.fullmatch("[0-9]{1,12}", fields.get(key, "")) is None:
            raise ValueError("内部一覧のサイズ情報が不正。")
    size = int(fields["Size"])
    if not 0 < size <= max_text_bytes:
        raise ValueError("展開後サイズが制限外。")
    if int(fields["Packed Size"]) <= 0:
        raise ValueError("圧縮サイズが不正。")
    return size


def validate_official_text(data: bytes, kind: OfficialDataKind) -> None:
    """元バイトを変更せずCP932と外側の開始・終了マーカーだけを検査する。"""
    text = data.decode("cp932", errors="strict")
    if "\x00" in text:
        raise ValueError("テキストにNULが含まれている。")
    if kind is OfficialDataKind.PROGRAM:
        code = "B"
    elif kind is OfficialDataKind.RESULT:
        code = "K"
    else:
        raise ValueError("未対応のデータ種別。")
    lines = re.split(r"\r\n|\n|\r", text)
    while lines and lines[-1] == "":
        lines.pop()
    if (
        not lines
        or lines[0] != f"START{code}"
        or lines[-1] != f"FINAL{code}"
        or lines.count(f"START{code}") != 1
        or lines.count(f"FINAL{code}") != 1
    ):
        raise ValueError("テキストの開始・終了マーカーが不正。")


def run_archive_command(
    command: list[str],
    destination: Path,
    max_bytes: int,
    timeout: float,
    record_path: Path,
) -> None:
    """標準出力を上限付きで受け取り、期限超過・失敗時は子プロセスを終了する。"""
    record: dict[str, object] = {"command": command, "started_at": _now()}
    timed_out = threading.Event()
    size = 0
    with TemporaryFile() as errors:
        try:
            with (
                destination.open("xb") as output,
                subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    cwd=destination.parent,
                ) as process,
            ):

                def kill_on_timeout() -> None:
                    timed_out.set()
                    with suppress(OSError):
                        process.kill()

                timer = threading.Timer(timeout, kill_on_timeout)
                timer.daemon = True
                timer.start()
                try:
                    pipe = process.stdout
                    if not isinstance(pipe, BufferedReader):
                        raise ValueError("7-Zipの標準出力がバイナリパイプではない。")
                    while chunk := pipe.read1(min(64 * 1024, max_bytes - size + 1)):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError("7-Zipの出力がサイズ上限を超えた。")
                        output.write(chunk)
                    exit_code = process.wait()
                    record["exit_code"] = exit_code
                    if timed_out.is_set():
                        raise ValueError("7-Zipが制限時間を超えた。")
                    if exit_code != 0:
                        raise ValueError(f"7-Zipが失敗した。終了コード: {exit_code}")
                    output.flush()
                    os.fsync(output.fileno())
                finally:
                    timer.cancel()
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    timer.join()
        except (OSError, ValueError, KeyboardInterrupt) as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            errors.seek(0)
            stderr = errors.read(64 * 1024 + 1)
            record.update(
                {
                    "finished_at": _now(),
                    "output_bytes": size,
                    "timed_out": timed_out.is_set(),
                    "stderr": stderr[: 64 * 1024].decode("utf-8", errors="replace"),
                    "stderr_truncated": len(stderr) > 64 * 1024,
                }
            )
            _write_json(record_path, record)


def _existing_text(spec: OfficialFileSpec, limit: int) -> bytes | None:
    directory = spec.staging_dir
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("正式展開先がリンクになっている。")
    if not directory.exists():
        return None
    if not directory.is_dir() or list(directory.iterdir()) != [spec.text_path]:
        raise ValueError("正式展開先が不完全、または想定外のファイルを含んでいる。")
    return _read_bytes(spec.text_path, limit)


def _extract_locked(
    spec: OfficialFileSpec,
    download_root: Path,
    history_root: Path,
    report: ExtractReport,
) -> None:
    policy = report.policy
    archive = _read_bytes(spec.archive_path, policy.max_archive_bytes)
    report.archive_size_bytes, report.archive_sha256 = _fingerprint(archive)
    baseline = _download_baseline(download_root, spec)
    report.download_history_found = baseline is not None
    if baseline is not None and baseline != (report.archive_size_bytes, report.archive_sha256):
        report.status = ExtractStatus.ARCHIVE_MISMATCH
        report.error = "原LZHが取得履歴と一致しない。"
        return
    previous = _extract_baseline(history_root, spec)
    existing = _existing_text(spec, policy.max_text_bytes)
    if previous is not None:
        if previous[0] != report.archive_sha256:
            report.status = ExtractStatus.ARCHIVE_MISMATCH
            report.error = "原LZHが過去の展開履歴と一致しない。"
        elif existing is None:
            report.status = ExtractStatus.HISTORY_MISSING_FILE
            report.error = "展開履歴はあるが正式TXTがない。"
        elif previous[1:] != _fingerprint(existing):
            report.status = ExtractStatus.TEXT_MISMATCH
            report.error = "正式TXTが過去の展開履歴と一致しない。"
        else:
            report.text_size_bytes, report.text_sha256 = _fingerprint(existing)
            validate_official_text(existing, spec.kind)
            report.text_verified = True
            report.status = ExtractStatus.SKIPPED_VERIFIED
        return

    seven_zip = Path(report.seven_zip_path)
    if not seven_zip.is_absolute() or not seven_zip.is_file():
        raise ValueError("7-Zip実行ファイルの絶対パスが必要だ。")
    spec.staging_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=f".{spec.archive_path.stem}-", dir=spec.staging_dir.parent
    ) as work:
        workspace = Path(work)
        snapshot = workspace / "source.lzh"
        snapshot.write_bytes(archive)
        listing_path = workspace / "listing.txt"
        common = ["-tLzh", "-sccUTF-8", "-bd", "-bsp0"]
        run_archive_command(
            [str(seven_zip), "l", "-slt", "-ba", *common, str(snapshot)],
            listing_path,
            policy.max_listing_bytes,
            policy.command_timeout_seconds,
            Path(report.history_dir) / "list_command.json",
        )
        listing = _read_bytes(listing_path, policy.max_listing_bytes).decode(
            "utf-8", errors="strict"
        )
        _write_json(Path(report.history_dir) / "listing.json", {"listing": listing})
        expected_size = parse_lzh_listing(listing, spec.text_path.name, policy.max_text_bytes)
        temporary = workspace / spec.text_path.name
        run_archive_command(
            [
                str(seven_zip),
                "e",
                "-so",
                "-bso2",
                "-bse2",
                *common,
                str(snapshot),
                spec.text_path.name,
            ],
            temporary,
            expected_size,
            policy.command_timeout_seconds,
            Path(report.history_dir) / "extract_command.json",
        )
        data = _read_bytes(temporary, policy.max_text_bytes)
        if len(data) != expected_size:
            raise ValueError("展開後サイズが内部一覧と一致しない。")
        validate_official_text(data, spec.kind)
        if _read_bytes(spec.archive_path, policy.max_archive_bytes) != archive:
            raise ValueError("検査・展開中に原LZHが変化した。")
        report.text_size_bytes, report.text_sha256 = _fingerprint(data)
        # 再確認により、初回確認後に追加・変更された正式展開物も置換しない。
        current = _existing_text(spec, policy.max_text_bytes)
        if current != existing:
            raise ValueError("検査・展開中に正式展開先が変化した。")
        if existing is not None and existing != data:
            report.status = ExtractStatus.TEXT_MISMATCH
            report.error = "既存TXTが原LZHから再現したバイト列と一致しない。"
            return
        report.text_verified = True
        _write_json(
            Path(report.history_dir) / "verified.json",
            {
                "schema_version": 1,
                "kind": report.kind,
                "target_date": report.target_date,
                "archive_sha256": report.archive_sha256,
                "text_name": spec.text_path.name,
                "text_size_bytes": report.text_size_bytes,
                "text_sha256": report.text_sha256,
                "encoding": "cp932",
                "verified_at": _now(),
                "source": "observed_existing" if existing is not None else "before_publish",
            },
        )
        if existing is not None:
            report.status = ExtractStatus.EXISTING_UNTRACKED
            return
        spec.staging_dir.mkdir()
        try:
            os.link(temporary, spec.text_path)
        except OSError:
            # 自分で作ったディレクトリが空の場合だけ片付ける。
            with suppress(OSError):
                spec.staging_dir.rmdir()
            raise
        report.extracted_at = _now()
        report.status = ExtractStatus.EXTRACTED


def extract_official_file(
    data_root: Path,
    target_date: date,
    kind: OfficialDataKind,
    seven_zip_path: Path,
    *,
    policy: ExtractPolicy | None = None,
) -> ExtractReport:
    """1日・1種別の原LZHを展開する。通信・原本変更・既存TXTの置換は行わない。

    結果のstatus/errorを必ず確認する。開始・終了履歴の保存失敗は
    ExtractHistoryErrorで通知する。異常終了後のロックは自動削除しない。
    """
    spec = build_official_file_spec(data_root, target_date, kind)
    manifest = data_root / "manifest" / "boatrace_official"
    relative = (
        Path(kind.value)
        / f"{target_date.year:04d}"
        / f"{target_date.month:02d}"
        / spec.archive_path.stem
    )
    history_root = manifest / "extract" / relative
    history_dir = history_root / uuid4().hex
    source_hash = hashlib.sha256()
    for name in ("official_extract.py", "official_files.py"):
        source_hash.update(Path(__file__).with_name(name).read_bytes())
    try:
        program_version = version("boatrace")
    except PackageNotFoundError:
        program_version = "uninstalled"
    report = ExtractReport(
        run_id=history_dir.name,
        kind=kind.value,
        target_date=target_date.isoformat(),
        archive_path=str(spec.archive_path),
        text_path=str(spec.text_path),
        history_dir=str(history_dir),
        started_at=_now(),
        program_version=program_version,
        implementation_sha256=source_hash.hexdigest(),
        policy=policy or ExtractPolicy(),
        seven_zip_path=str(seven_zip_path),
    )
    try:
        history_dir.mkdir(parents=True)
    except OSError as exc:
        raise ExtractHistoryError(f"展開履歴ディレクトリを作成できない: {history_dir}") from exc
    _write_json(history_dir / "started.json", asdict(report))
    lock = manifest / ".extract.lock"
    locked = False
    try:
        try:
            lock.mkdir()
        except FileExistsError:
            report.status = ExtractStatus.BUSY
            report.error = f"展開ロックが存在する。実行中か残存ロックかを確認する: {lock}"
        else:
            locked = True
            _write_json(
                lock / "owner.json",
                {
                    "run_id": report.run_id,
                    "pid": os.getpid(),
                    "started_at": report.started_at,
                    "history_dir": report.history_dir,
                },
            )
            _extract_locked(spec, manifest / relative, history_root, report)
    except (OSError, ValueError, ExtractHistoryError) as exc:
        report.status = ExtractStatus.FAILED
        report.error = f"{type(exc).__name__}: {exc}"
    except KeyboardInterrupt:
        report.status = ExtractStatus.FAILED
        report.error = "KeyboardInterrupt: 利用者が処理を中断した。"
        raise
    finally:
        report.finished_at = _now()
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
