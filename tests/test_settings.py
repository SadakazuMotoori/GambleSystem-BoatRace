"""アプリケーション設定のテスト。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from boatrace.settings import load_settings

_SETTING_ENVIRONMENT_VARIABLES = (
    "BOATRACE_ENV",
    "BOATRACE_DATA_DIR",
    "BOATRACE_LOG_LEVEL",
    "BOATRACE_7ZIP_PATH",
)


def _clear_setting_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """テスト対象へ影響する環境変数を削除する。"""
    for variable_name in _SETTING_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable_name, raising=False)


def test_load_settings_from_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """指定した.envから全設定を読み込める。"""
    _clear_setting_environment(monkeypatch)

    data_dir = tmp_path / "BoatRaceData"
    seven_zip_path = tmp_path / "7-Zip" / "7z.exe"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'BOATRACE_ENV="test"',
                f'BOATRACE_DATA_DIR="{data_dir.as_posix()}"',
                'BOATRACE_LOG_LEVEL="DEBUG"',
                f'BOATRACE_7ZIP_PATH="{seven_zip_path.as_posix()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(env_file)

    assert settings.environment == "test"
    assert settings.data_dir == data_dir
    assert settings.log_level == "DEBUG"
    assert settings.seven_zip_path == seven_zip_path


def test_load_settings_rejects_missing_required_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """必須パスが設定されていなければ読込に失敗する。"""
    _clear_setting_environment(monkeypatch)

    env_file = tmp_path / ".env"
    env_file.write_text(
        'BOATRACE_ENV="test"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_settings(env_file)
