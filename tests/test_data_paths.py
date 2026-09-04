"""外部データ保存領域のテスト。"""

from pathlib import Path

import pytest

from boatrace.data_paths import (
    DATA_DIRECTORY_NAMES,
    ensure_data_directories,
    required_data_directories,
)


def test_required_data_directories_returns_expected_paths(tmp_path: Path) -> None:
    """必要な9ディレクトリのパスを定義順で返す。"""
    data_root = tmp_path / "BoatRaceData"

    directories = required_data_directories(data_root)

    assert tuple(directory.name for directory in directories) == DATA_DIRECTORY_NAMES
    assert all(directory.parent == data_root for directory in directories)
    assert not data_root.exists()


def test_ensure_data_directories_creates_structure(tmp_path: Path) -> None:
    """必要なディレクトリを作成し、再実行しても失敗しない。"""
    data_root = tmp_path / "BoatRaceData"

    first_result = ensure_data_directories(data_root)
    second_result = ensure_data_directories(data_root)

    assert data_root.is_dir()
    assert first_result == second_result
    assert all(directory.is_dir() for directory in first_result)


def test_ensure_data_directories_rejects_relative_path() -> None:
    """相対パスをデータ保存先として使用できない。"""
    with pytest.raises(ValueError, match="絶対パス"):
        ensure_data_directories(Path("BoatRaceData"))
