"""HTTPの不完全応答を拒否し、再試行対象を限定する。外部通信は行わない。"""

import hashlib
import io
import socket
import ssl
import threading
from collections.abc import Iterator
from http.client import HTTPMessage, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO, cast
from urllib.error import HTTPError, URLError

import pytest

import boatrace.official_http as transport
from boatrace.official_http import TransferError, TransferPolicy, fetch_archive_once


class ResponseSocket:
    """標準HTTPResponseへテスト用の受信バイト列を渡す。"""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def makefile(self, mode: str) -> BinaryIO:
        return io.BytesIO(self.raw)


def provide_response(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> HTTPResponse:
    response = HTTPResponse(cast(socket.socket, ResponseSocket(raw)))
    response.begin()

    def open_response(url: str, timeout_seconds: float) -> HTTPResponse:
        assert timeout_seconds == 30.0
        return response

    monkeypatch.setattr(transport, "_open_response", open_response)
    return response


def test_streamed_body_is_saved_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"binary\x00\xff\r\n"
    provide_response(monkeypatch, b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n" + body)
    destination = tmp_path / "unique.lzh"

    result = fetch_archive_once("https://example.invalid/data", destination, TransferPolicy())

    assert destination.read_bytes() == body
    assert result.size_bytes == len(body)
    assert result.sha256 == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize(
    "headers, body",
    [
        (b"Content-Length: 0\r\n", b""),
        (b"Content-Length: -1\r\n", b"abc"),
        (b"Content-Length: garbage\r\n", b"abc"),
        (b"Content-Length: 3, 3\r\n", b"abc"),
        (b"Content-Length: 9\r\n", b"short"),
        (b"Content-Length: 16777217\r\n", b"short"),
        (b"Content-Encoding: gzip\r\n", b"data"),
        (b"Content-Length: 3\r\nTransfer-Encoding: chunked\r\n", b"3\r\nabc\r\n0\r\n\r\n"),
        (b"", b""),
    ],
)
def test_invalid_body_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    headers: bytes,
    body: bytes,
) -> None:
    provide_response(monkeypatch, b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + body)

    with pytest.raises(TransferError) as raised:
        fetch_archive_once("https://example.invalid/data", tmp_path / "partial", TransferPolicy())
    assert raised.value.status_code == 200
    assert not raised.value.retryable


def test_size_limit_applies_without_length_header(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provide_response(monkeypatch, b"HTTP/1.1 200 OK\r\n\r\n12345")

    with pytest.raises(TransferError, match="サイズ上限"):
        fetch_archive_once(
            "https://example.invalid/data", tmp_path / "partial", TransferPolicy(max_bytes=4)
        )


def test_chunked_body_is_supported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provide_response(
        monkeypatch,
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n",
    )
    destination = tmp_path / "archive"

    result = fetch_archive_once("https://example.invalid/data", destination, TransferPolicy())

    assert destination.read_bytes() == b"abcde"
    assert result.size_bytes == 5


def test_deadline_stops_slow_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provide_response(monkeypatch, b"HTTP/1.1 200 OK\r\n\r\nabc")
    ticks = iter([0.0, 0.0, 121.0])
    monkeypatch.setattr(transport.time, "monotonic", lambda: next(ticks))

    with pytest.raises(TransferError, match="期限超過") as raised:
        fetch_archive_once("https://example.invalid/data", tmp_path / "partial", TransferPolicy())
    assert raised.value.retryable


def test_disconnection_during_body_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = provide_response(monkeypatch, b"HTTP/1.1 200 OK\r\n\r\nabc")

    def disconnected(amount: int) -> bytes:
        raise ConnectionResetError("reset")

    monkeypatch.setattr(response, "read1", disconnected)
    with pytest.raises(TransferError) as raised:
        fetch_archive_once("https://example.invalid/data", tmp_path / "partial", TransferPolicy())
    assert raised.value.retryable and raised.value.status_code == 200


@pytest.mark.parametrize(
    "status, retryable",
    [
        (301, False),
        (302, False),
        (403, False),
        (404, False),
        (429, False),
        (408, True),
        (500, True),
        (502, True),
        (503, True),
        (504, True),
    ],
)
def test_http_retry_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    retryable: bool,
) -> None:
    body = io.BytesIO(b"error page")

    def rejected(url: str, timeout_seconds: float) -> HTTPResponse:
        raise HTTPError(url, status, "test", HTTPMessage(), body)

    monkeypatch.setattr(transport, "_open_response", rejected)
    with pytest.raises(TransferError) as raised:
        fetch_archive_once("https://example.invalid/data", tmp_path / "partial", TransferPolicy())
    assert raised.value.status_code == status
    assert raised.value.retryable is retryable
    assert body.closed


@pytest.mark.parametrize(
    "reason, retryable",
    [
        (TimeoutError("timeout"), True),
        (ssl.SSLCertVerificationError("certificate error"), False),
    ],
)
def test_connection_retry_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason: OSError,
    retryable: bool,
) -> None:
    def failed(url: str, timeout_seconds: float) -> HTTPResponse:
        raise URLError(reason)

    monkeypatch.setattr(transport, "_open_response", failed)
    with pytest.raises(TransferError) as raised:
        fetch_archive_once("https://example.invalid/data", tmp_path / "partial", TransferPolicy())
    assert raised.value.retryable is retryable


def test_exclusive_temporary_file_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provide_response(monkeypatch, b"HTTP/1.1 200 OK\r\n\r\nnew bytes")
    destination = tmp_path / "existing"
    destination.write_bytes(b"original")

    with pytest.raises(FileExistsError):
        fetch_archive_once("https://example.invalid/data", destination, TransferPolicy())
    assert destination.read_bytes() == b"original"


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_time_limits_are_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        TransferPolicy(timeout_seconds=value)
    with pytest.raises(ValueError):
        TransferPolicy(body_deadline_seconds=value)


class LocalHandler(BaseHTTPRequestHandler):
    visits: list[str] = []

    def do_GET(self) -> None:
        self.visits.append(self.path)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/archive")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", "3")
        self.end_headers()
        self.wfile.write(b"abc")

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def local_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    # プロキシ設定があるPCでも、試験サーバーへの通信を外へ出さない。
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    LocalHandler.visits = []
    with ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_real_opener_reads_local_response(tmp_path: Path, local_server: str) -> None:
    destination = tmp_path / "archive"

    result = fetch_archive_once(f"{local_server}/archive", destination, TransferPolicy())

    assert result.size_bytes == 3
    assert destination.read_bytes() == b"abc"
    assert LocalHandler.visits == ["/archive"]


def test_real_opener_does_not_follow_redirect(tmp_path: Path, local_server: str) -> None:
    with pytest.raises(TransferError) as raised:
        fetch_archive_once(f"{local_server}/redirect", tmp_path / "archive", TransferPolicy())

    assert raised.value.status_code == 302
    assert LocalHandler.visits == ["/redirect"]
