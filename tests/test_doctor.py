"""開発・実行環境診断のテスト"""

import subprocess
from pathlib import Path

import pytest

from boatrace.data_paths import DATA_DIRECTORY_NAMES, ensure_data_directories
from boatrace.doctor import (
    check_data_directories,
    check_duckdb,
    check_python,
    check_seven_zip,
    run_diagnostics,
)


def test_check_python_passes_on_supported_runtime() -> None:
    """Python 3.14の標準GIL環境を正常と判定する。"""
    result = check_python()

    assert result.passed
    assert result.name == "Python"
    assert "3.14." in result.detail
    assert "GIL=有効" in result.detail


def test_check_data_directories_passes_for_complete_structure(
    tmp_path: Path,
) -> None:
    """9ディレクトリが揃ったデータ領域を正常と判定する。"""
    data_root = tmp_path / "BoatRaceData"
    ensure_data_directories(data_root)

    result = check_data_directories(data_root)

    assert result.passed
    assert result.name == "データ領域"
    assert "9ディレクトリ" in result.detail


def test_check_data_directories_reports_missing_directory(
    tmp_path: Path,
) -> None:
    """不足ディレクトリを異常として報告する。"""
    data_root = tmp_path / "BoatRaceData"
    data_root.mkdir()
    missing_name = DATA_DIRECTORY_NAMES[-1]

    for directory_name in DATA_DIRECTORY_NAMES[:-1]:
        (data_root / directory_name).mkdir()

    result = check_data_directories(data_root)

    assert not result.passed
    assert missing_name in result.detail


def test_check_seven_zip_accepts_expected_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """期待する7-Zipの起動結果を正常と判定する。"""
    executable = tmp_path / "7z.exe"
    executable.write_bytes(b"")

    def fake_run(
        *_args: object,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[str(executable)],
            returncode=0,
            stdout="7-Zip 26.02 (x64)\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = check_seven_zip(executable)

    assert result.passed
    assert result.name == "7-Zip"
    assert "26.02" in result.detail


def test_check_duckdb_connects_to_memory_database() -> None:
    """固定バージョンのDuckDBへインメモリ接続できる。"""
    result = check_duckdb()

    assert result.passed
    assert result.name == "DuckDB"
    assert result.detail == "1.4.5"


def test_run_diagnostics_stops_when_settings_are_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """必須設定がなければ依存する診断を実行せず失敗を返す。"""
    monkeypatch.delenv("BOATRACE_DATA_DIR", raising=False)
    monkeypatch.delenv("BOATRACE_7ZIP_PATH", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        'BOATRACE_ENV="test"\n',
        encoding="utf-8",
    )

    results = run_diagnostics(env_file)

    assert tuple(result.name for result in results) == ("Python", "設定")
    assert results[0].passed
    assert not results[1].passed
