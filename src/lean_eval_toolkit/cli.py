"""Command-line entry point."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from lean_eval_toolkit import __version__
from lean_eval_toolkit.datasets import BUILTIN_DATASETS, DatasetError, load_dataset, write_jsonl

app = typer.Typer(
    name="lean-eval",
    help="Evaluate remote models on Lean theorem-proving benchmarks.",
    no_args_is_help=True,
)
datasets_app = typer.Typer(help="Inspect and normalize Lean benchmark datasets.")
app.add_typer(datasets_app, name="datasets")
console = Console()


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


@datasets_app.command("list")
def list_datasets() -> None:
    """List datasets with built-in layout support."""
    table = Table("Name", "Description", "Upstream")
    for name, info in BUILTIN_DATASETS.items():
        table.add_row(name, info["description"], info["upstream"])
    console.print(table)


@datasets_app.command("import")
def import_dataset(
    source: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o", help="Normalized JSONL destination.")],
    name: Annotated[str, typer.Option("--name", help="Dataset name stored in each record.")] = (
        "custom"
    ),
    split: Annotated[str | None, typer.Option(help="Override the inferred split.")] = None,
) -> None:
    """Normalize a JSONL file, Lean file, or directory of Lean files."""
    try:
        problems = load_dataset(source, dataset=name.lower(), split=split)
        count = write_jsonl(problems, output)
    except DatasetError as exc:
        console.print(f"[red]Dataset error:[/red] {exc}")
        raise typer.Exit(2) from exc
    console.print(f"Wrote [bold]{count}[/bold] problems to {output}")
