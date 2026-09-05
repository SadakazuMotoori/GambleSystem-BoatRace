"""公式アーカイブの1回分のHTTP取得。再試行と正式保存は呼出側が担う。"""

import hashlib
import math
import os
import ssl
import time
from dataclasses import dataclass
from http.client import HTTPException, HTTPResponse
from pathlib import Path
from typing import IO, cast
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True, slots=True)
class TransferPolicy:
    """数値は本プロジェクトの制限であり、公式が許可したアクセス条件ではない。"""

    timeout_seconds: float = 30.0
    body_deadline_seconds: float = 120.0
    max_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        for value in (self.timeout_seconds, self.body_deadline_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("通信時間の制限には有限の正数が必要だ。")
        if type(self.max_bytes) is not int or self.max_bytes <= 0:
            raise ValueError("サイズ上限には正の整数が必要だ。")


@dataclass(frozen=True, slots=True)
class TransferResult:
    status_code: int
    size_bytes: int
    sha256: str


class TransferError(Exception):
    """HTTP・本文の異常。ディスク書込エラーはこの例外へ変換しない。"""

    def __init__(
        self, message: str, *, retryable: bool = False, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _open_response(url: str, timeout_seconds: float) -> HTTPResponse:
    request = Request(
        url,
        headers={
            "User-Agent": "BoatRace/0.1 (personal data verification)",
            "Accept-Encoding": "identity",
        },
    )
    return cast(HTTPResponse, build_opener(_NoRedirect()).open(request, timeout=timeout_seconds))


def fetch_archive_once(url: str, destination: Path, policy: TransferPolicy) -> TransferResult:
    """一意な一時パスへ排他的に保存する。失敗時の一時ファイル削除は呼出側が担う。

    リダイレクト・200以外・空本文・サイズ不一致・圧縮HTTP本文を拒否する。
    本文の期限はread1前後で確認するため、通信タイムアウト分だけ超過し得る。
    """
    started = time.monotonic()
    try:
        response = _open_response(url, policy.timeout_seconds)
    except HTTPError as exc:
        status = exc.code
        exc.close()
        raise TransferError(
            f"HTTP {status}", retryable=status in {408, 500, 502, 503, 504}, status_code=status
        ) from exc
    except URLError as exc:
        raise TransferError(
            f"接続失敗: {exc.reason}",
            retryable=not isinstance(exc.reason, ssl.SSLCertVerificationError),
        ) from exc
    except (TimeoutError, ConnectionError, HTTPException) as exc:
        raise TransferError(f"接続失敗: {exc}", retryable=True) from exc

    with response:
        status = response.status
        if status != 200:
            raise TransferError(f"HTTP {status}", status_code=status)
        encoding = response.getheader("Content-Encoding", "identity").strip().lower()
        if encoding != "identity":
            raise TransferError("未対応のContent-Encoding。", status_code=status)
        length_header = response.getheader("Content-Length")
        expected_size: int | None = None
        if length_header is not None:
            if not length_header.isascii() or not length_header.isdecimal():
                raise TransferError("Content-Lengthが不正。", status_code=status)
            try:
                expected_size = int(length_header)
            except ValueError as exc:
                raise TransferError("Content-Lengthが不正。", status_code=status) from exc
            if not 0 < expected_size <= policy.max_bytes:
                raise TransferError("Content-Lengthがサイズ制限外。", status_code=status)
        if response.getheader("Transfer-Encoding") is not None and length_header is not None:
            raise TransferError("本文長の指定が競合している。", status_code=status)

        size = 0
        digest = hashlib.sha256()
        with destination.open("xb") as output:
            while True:
                if time.monotonic() - started > policy.body_deadline_seconds:
                    raise TransferError("本文取得の期限超過。", retryable=True, status_code=status)
                try:
                    chunk = response.read1(min(64 * 1024, policy.max_bytes - size + 1))
                except (OSError, HTTPException) as exc:
                    raise TransferError(
                        f"本文受信失敗: {exc}", retryable=True, status_code=status
                    ) from exc
                if time.monotonic() - started > policy.body_deadline_seconds:
                    raise TransferError("本文取得の期限超過。", retryable=True, status_code=status)
                if not chunk:
                    break
                size += len(chunk)
                if size > policy.max_bytes:
                    raise TransferError("本文がサイズ上限を超えた。", status_code=status)
                output.write(chunk)
                digest.update(chunk)
            if size == 0:
                raise TransferError("本文が空。", status_code=status)
            if expected_size is not None and size != expected_size:
                raise TransferError("本文長がContent-Lengthと一致しない。", status_code=status)
            output.flush()
            os.fsync(output.fileno())
        return TransferResult(status, size, digest.hexdigest())
