"""外部データ保存領域のパス管理。"""

from pathlib import Path
from typing import Final

DATA_DIRECTORY_NAMES: Final[tuple[str, ...]] = (
    "raw",
    "manifest",
    "staging",
    "lake",
    "db",
    "models",
    "reports",
    "logs",
    "tmp",
)


def required_data_directories(data_root: Path) -> tuple[Path, ...]:
    """データルート直下に必要なディレクトリ一覧を返す。"""
    return tuple(data_root / name for name in DATA_DIRECTORY_NAMES)


def ensure_data_directories(data_root: Path) -> tuple[Path, ...]:
    """データルートと必要なディレクトリを作成して返す。"""
    if not data_root.is_absolute():
        message = "データ保存先には絶対パスを指定する必要がある。"
        raise ValueError(message)

    data_root.mkdir(parents=True, exist_ok=True)
    directories = required_data_directories(data_root)

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    return directories
