"""コマンドラインインターフェースのテスト。"""

import pytest
from typer.testing import CliRunner

from boatrace.cli import app
from boatrace.doctor import DiagnosticResult

runner = CliRunner()


def test_root_prints_ready_message() -> None:
    """サブコマンドなしでは起動確認メッセージを表示する。"""
    result = runner.invoke(app)

    assert result.exit_code == 0
    assert result.output == "BoatRace project is ready.\n"


def test_doctor_command_succeeds_when_all_checks_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全診断成功時は結果を表示して終了コード0を返す。"""
    results = (
        DiagnosticResult(
            name="Python",
            passed=True,
            detail="3.14.7",
        ),
    )
    monkeypatch.setattr(
        "boatrace.cli.run_diagnostics",
        lambda: results,
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert result.output == "[OK] Python: 3.14.7\n"


def test_doctor_command_fails_when_any_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """診断失敗時は結果を表示して終了コード1を返す。"""
    results = (
        DiagnosticResult(
            name="7-Zip",
            passed=False,
            detail="実行ファイルが存在しない",
        ),
    )
    monkeypatch.setattr(
        "boatrace.cli.run_diagnostics",
        lambda: results,
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert result.output == "[NG] 7-Zip: 実行ファイルが存在しない\n"
