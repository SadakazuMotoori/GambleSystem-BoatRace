"""コマンドラインインターフェース。"""

import typer

from boatrace.doctor import run_diagnostics

app = typer.Typer(
    help="尼崎ボートレース予測システム。",
    add_completion=False,
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def root(context: typer.Context) -> None:
    """BoatRaceのコマンドを実行する。"""
    if context.invoked_subcommand is None:
        typer.echo("BoatRace project is ready.")


@app.command("doctor")
def doctor_command() -> None:
    """開発・実行環境を診断する。"""
    results = run_diagnostics()

    for result in results:
        status = "OK" if result.passed else "NG"
        typer.echo(f"[{status}] {result.name}: {result.detail}")

    if not all(result.passed for result in results):
        raise typer.Exit(code=1)


def main() -> None:
    """CLIを起動する。"""
    app()


if __name__ == "__main__":
    main()
