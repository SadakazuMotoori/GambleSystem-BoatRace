import pytest

from boatrace.cli import main


def test_main_prints_ready_message(capsys: pytest.CaptureFixture[str]) -> None:
    main()

    captured = capsys.readouterr()

    assert captured.out == "BoatRace project is ready.\n"
    assert captured.err == ""
