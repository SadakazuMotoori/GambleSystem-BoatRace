"""公式B/KファイルのURL・保存先生成のテスト。"""

from datetime import date, datetime
from pathlib import Path
from typing import cast

import pytest

from boatrace.official_files import OfficialDataKind, build_official_file_spec


@pytest.mark.parametrize(
    ("kind", "expected_url", "expected_archive", "expected_text"),
    [
        (
            OfficialDataKind.PROGRAM,
            "https://www1.mbrace.or.jp/od2/B/202609/b260902.lzh",
            "raw/boatrace_official/program/2026/09/b260902.lzh",
            "staging/boatrace_official/program/2026/09/b260902/B260902.TXT",
        ),
        (
            OfficialDataKind.RESULT,
            "https://www1.mbrace.or.jp/od2/K/202609/k260902.lzh",
            "raw/boatrace_official/result/2026/09/k260902.lzh",
            "staging/boatrace_official/result/2026/09/k260902/K260902.TXT",
        ),
    ],
)
def test_build_official_file_spec_matches_verified_sample(
    tmp_path: Path,
    kind: OfficialDataKind,
    expected_url: str,
    expected_archive: str,
    expected_text: str,
) -> None:
    """手動検証済みのB/KのURL・保存先と一致し、ディレクトリを作成しない。"""
    data_root = tmp_path / "BoatRaceData"
    target_date = date(2026, 9, 2)

    spec = build_official_file_spec(data_root, target_date, kind)

    assert spec.kind is kind
    assert spec.target_date == target_date
    assert spec.url == expected_url
    assert spec.archive_path == data_root / expected_archive
    assert spec.text_path == data_root / expected_text
    assert spec.staging_dir == (data_root / expected_text).parent
    assert not data_root.exists()


@pytest.mark.parametrize("kind", [OfficialDataKind.PROGRAM, OfficialDataKind.RESULT])
@pytest.mark.parametrize(
    ("target_date", "expected_month", "expected_short_date"),
    [
        (date(2026, 9, 30), "202609", "260930"),
        (date(2026, 10, 1), "202610", "261001"),
        (date(2026, 12, 31), "202612", "261231"),
        (date(2027, 1, 1), "202701", "270101"),
        (date(2024, 2, 29), "202402", "240229"),
        (date(2000, 1, 1), "200001", "000101"),
    ],
)
def test_build_official_file_spec_handles_date_boundaries(
    tmp_path: Path,
    kind: OfficialDataKind,
    target_date: date,
    expected_month: str,
    expected_short_date: str,
) -> None:
    """月・年の境界、うるう日、2桁年の先頭ゼロを正しく扱う。"""
    code = "B" if kind is OfficialDataKind.PROGRAM else "K"
    archive_stem = f"{code.lower()}{expected_short_date}"
    relative_directory = (
        Path("boatrace_official") / kind.value / expected_month[:4] / expected_month[4:]
    )

    spec = build_official_file_spec(tmp_path, target_date, kind)

    assert spec.url == f"https://www1.mbrace.or.jp/od2/{code}/{expected_month}/{archive_stem}.lzh"
    assert spec.archive_path == tmp_path / "raw" / relative_directory / f"{archive_stem}.lzh"
    assert spec.staging_dir == tmp_path / "staging" / relative_directory / archive_stem
    assert spec.text_path.name == f"{code}{expected_short_date}.TXT"


def test_build_official_file_spec_rejects_relative_root() -> None:
    """作業ディレクトリに依存する相対データルートを拒否する。"""
    with pytest.raises(ValueError, match="絶対パス"):
        build_official_file_spec(Path("BoatRaceData"), date(2026, 9, 2), OfficialDataKind.PROGRAM)


@pytest.mark.parametrize("invalid_date", [datetime(2026, 9, 2, 23, 30), "2026-09-02", None])
def test_build_official_file_spec_rejects_non_date(
    tmp_path: Path,
    invalid_date: object,
) -> None:
    """時刻や文字列を暗黙に日付へ変換しない。"""
    with pytest.raises(TypeError, match="時刻を含まないdate"):
        build_official_file_spec(tmp_path, cast(date, invalid_date), OfficialDataKind.PROGRAM)


@pytest.mark.parametrize("invalid_kind", ["program", "unknown", None])
def test_build_official_file_spec_rejects_unsupported_kind(
    tmp_path: Path,
    invalid_kind: object,
) -> None:
    """未変換の文字列などを別種別として誤って処理しない。"""
    with pytest.raises(ValueError, match="OfficialDataKind"):
        build_official_file_spec(tmp_path, date(2026, 9, 2), cast(OfficialDataKind, invalid_kind))


def test_build_official_file_spec_preserves_existing_files(tmp_path: Path) -> None:
    """保存先に既存の元データや展開物があっても内容を変更しない。"""
    archive_path = tmp_path / "raw/boatrace_official/result/2026/09/k260902.lzh"
    text_path = tmp_path / "staging/boatrace_official/result/2026/09/k260902/K260902.TXT"
    archive_path.parent.mkdir(parents=True)
    text_path.parent.mkdir(parents=True)
    archive_path.write_bytes(b"existing archive")
    text_path.write_bytes(b"existing text")

    spec = build_official_file_spec(tmp_path, date(2026, 9, 2), OfficialDataKind.RESULT)

    assert spec.archive_path == archive_path
    assert spec.text_path == text_path
    assert archive_path.read_bytes() == b"existing archive"
    assert text_path.read_bytes() == b"existing text"
