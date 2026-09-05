"""公式B/KファイルのURLと保存先を生成する。"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Final

OFFICIAL_BASE_URL: Final[str] = "https://www1.mbrace.or.jp/od2"


class OfficialDataKind(StrEnum):
    """日次の公式データ種別。"""

    PROGRAM = "program"
    RESULT = "result"


@dataclass(frozen=True, slots=True)
class OfficialFileSpec:
    """1日・1種別の取得元と保存先。ファイルの存在は保証しない。"""

    kind: OfficialDataKind
    target_date: date
    url: str
    archive_path: Path
    text_path: Path

    @property
    def staging_dir(self) -> Path:
        """展開後テキストを保存するディレクトリを返す。"""
        return self.text_path.parent


def build_official_file_spec(
    data_root: Path,
    target_date: date,
    kind: OfficialDataKind,
) -> OfficialFileSpec:
    """URLとパスだけを生成する。通信・ファイル操作・設定読込は行わない。

    日付にはdateを渡す。datetimeから日付への暗黙変換は行わない。
    データ提供期間を仮定せず、指定日の年月をそのままURLとパスに使用する。
    """
    if not data_root.is_absolute():
        message = "データ保存先には絶対パスを指定する必要がある。"
        raise ValueError(message)

    if type(target_date) is not date:
        message = "対象日には時刻を含まないdateを指定する必要がある。"
        raise TypeError(message)

    if kind is OfficialDataKind.PROGRAM:
        code = "B"
    elif kind is OfficialDataKind.RESULT:
        code = "K"
    else:
        message = "データ種別にはOfficialDataKindのPROGRAMまたはRESULTを指定する必要がある。"
        raise ValueError(message)

    year = f"{target_date.year:04d}"
    month = f"{target_date.month:02d}"
    short_date = f"{target_date.year % 100:02d}{month}{target_date.day:02d}"
    archive_stem = f"{code.lower()}{short_date}"
    archive_name = f"{archive_stem}.lzh"
    text_name = f"{code}{short_date}.TXT"
    relative_directory = Path("boatrace_official") / kind.value / year / month

    return OfficialFileSpec(
        kind=kind,
        target_date=target_date,
        url=f"{OFFICIAL_BASE_URL}/{code}/{year}{month}/{archive_name}",
        archive_path=data_root / "raw" / relative_directory / archive_name,
        text_path=data_root / "staging" / relative_directory / archive_stem / text_name,
    )
