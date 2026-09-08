"""Command-line entry point."""

import typer

from lean_eval_toolkit import __version__

app = typer.Typer(
    name="lean-eval",
    help="Evaluate remote models on Lean theorem-proving benchmarks.",
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Lean evaluation toolkit."""
