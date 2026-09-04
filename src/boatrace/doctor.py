"""開発・実行環境の診断。"""

import platform
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Final, cast

import duckdb
from pydantic import ValidationError

from boatrace.data_paths import DATA_DIRECTORY_NAMES, required_data_directories
from boatrace.settings import Settings, load_settings

EXPECTED_PYTHON_VERSION: Final = (3, 14)
EXPECTED_SEVEN_ZIP_VERSION: Final = "26.02"
EXPECTED_DUCKDB_VERSION: Final = "1.4.5"


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    """個別診断の結果。"""

    name: str
    passed: bool
    detail: str


def check_python() -> DiagnosticResult:
    """PythonのバージョンとGIL状態を確認する。"""
    current_version = platform.python_version()
    version_matches = sys.version_info[:2] == EXPECTED_PYTHON_VERSION
    gil_checker = cast(
        Callable[[], bool] | None,
        getattr(sys, "_is_gil_enabled", None),
    )
    gil_enabled = gil_checker() if gil_checker is not None else True
    passed = version_matches and gil_enabled
    gil_label = "有効" if gil_enabled else "無効"

    return DiagnosticResult(
        name="Python",
        passed=passed,
        detail=f"{current_version} / GIL={gil_label}",
    )


def check_settings(settings: Settings) -> DiagnosticResult:
    """読み込んだ基本設定を確認する。"""
    return DiagnosticResult(
        name="設定",
        passed=True,
        detail=f"環境={settings.environment} / ログ={settings.log_level}",
    )


def check_data_directories(data_root: Path) -> DiagnosticResult:
    """外部データ領域と9ディレクトリを確認する。"""
    if not data_root.is_absolute():
        return DiagnosticResult(
            name="データ領域",
            passed=False,
            detail="BOATRACE_DATA_DIRが絶対パスではない。",
        )

    if not data_root.is_dir():
        return DiagnosticResult(
            name="データ領域",
            passed=False,
            detail=f"データルートが存在しない: {data_root}",
        )

    missing_directories = [
        directory.name
        for directory in required_data_directories(data_root)
        if not directory.is_dir()
    ]
    if missing_directories:
        return DiagnosticResult(
            name="データ領域",
            passed=False,
            detail=f"不足: {', '.join(missing_directories)}",
        )

    return DiagnosticResult(
        name="データ領域",
        passed=True,
        detail=f"{data_root} / {len(DATA_DIRECTORY_NAMES)}ディレクトリ",
    )


def check_seven_zip(executable: Path) -> DiagnosticResult:
    """7-Zipの存在、起動、バージョンを確認する。"""
    if not executable.is_absolute():
        return DiagnosticResult(
            name="7-Zip",
            passed=False,
            detail="BOATRACE_7ZIP_PATHが絶対パスではない。",
        )

    if not executable.is_file():
        return DiagnosticResult(
            name="7-Zip",
            passed=False,
            detail=f"実行ファイルが存在しない: {executable}",
        )

    try:
        process = subprocess.run(
            [str(executable)],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return DiagnosticResult(
            name="7-Zip",
            passed=False,
            detail=f"起動失敗: {error}",
        )

    output = f"{process.stdout}\n{process.stderr}"
    version_marker = f"7-Zip {EXPECTED_SEVEN_ZIP_VERSION}"

    if process.returncode != 0:
        return DiagnosticResult(
            name="7-Zip",
            passed=False,
            detail=f"終了コード={process.returncode}",
        )

    if version_marker not in output:
        return DiagnosticResult(
            name="7-Zip",
            passed=False,
            detail=f"要求バージョンを確認できない: {EXPECTED_SEVEN_ZIP_VERSION}",
        )

    return DiagnosticResult(
        name="7-Zip",
        passed=True,
        detail=f"{EXPECTED_SEVEN_ZIP_VERSION} / {executable}",
    )


def check_duckdb() -> DiagnosticResult:
    """DuckDBのバージョンとインメモリ接続を確認する。"""
    installed_version = package_version("duckdb")
    if installed_version != EXPECTED_DUCKDB_VERSION:
        return DiagnosticResult(
            name="DuckDB",
            passed=False,
            detail=(f"バージョン不一致: {installed_version}（要求={EXPECTED_DUCKDB_VERSION}）"),
        )

    try:
        connection = duckdb.connect(database=":memory:")
        try:
            connection.execute("SELECT 1")
        finally:
            connection.close()
    except duckdb.Error as error:
        return DiagnosticResult(
            name="DuckDB",
            passed=False,
            detail=f"接続失敗: {error}",
        )

    return DiagnosticResult(
        name="DuckDB",
        passed=True,
        detail=installed_version,
    )


def run_diagnostics(
    env_file: str | Path = ".env",
) -> tuple[DiagnosticResult, ...]:
    """全診断を実行する。"""
    results = [check_python()]

    try:
        settings = load_settings(env_file)
    except (OSError, ValidationError) as error:
        results.append(
            DiagnosticResult(
                name="設定",
                passed=False,
                detail=str(error),
            )
        )
        return tuple(results)

    results.extend(
        (
            check_settings(settings),
            check_data_directories(settings.data_dir),
            check_seven_zip(settings.seven_zip_path),
            check_duckdb(),
        )
    )
    return tuple(results)
