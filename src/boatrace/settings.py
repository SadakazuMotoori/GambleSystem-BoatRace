"""アプリケーション設定の読み込み。"""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """環境変数または.envから読み込む実行設定。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: str = Field(
        default="development",
        validation_alias="BOATRACE_ENV",
    )
    data_dir: Path = Field(
        validation_alias="BOATRACE_DATA_DIR",
    )
    log_level: LogLevel = Field(
        default="INFO",
        validation_alias="BOATRACE_LOG_LEVEL",
    )
    seven_zip_path: Path = Field(
        validation_alias="BOATRACE_7ZIP_PATH",
    )


def load_settings(env_file: str | Path = ".env") -> Settings:
    """指定した.envを使用して設定を読み込む。"""
    # 必須項目は.envから動的に渡されるため、この呼出行だけPyrightの検査対象外とする。
    return Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
