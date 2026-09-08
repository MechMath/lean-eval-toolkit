"""Command-line entry point."""

import asyncio
from pathlib import Path
from typing import Annotated, Protocol

import typer
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from lean_eval_toolkit import __version__
from lean_eval_toolkit.config import Settings
from lean_eval_toolkit.datasets import BUILTIN_DATASETS, DatasetError, load_dataset, write_jsonl
from lean_eval_toolkit.evaluation import AttemptResult, RunWriter, evaluate
from lean_eval_toolkit.model import OpenAICompatibleClient
from lean_eval_toolkit.verifier import AxleVerifier

app = typer.Typer(
    name="lean-eval",
    help="Evaluate remote models on Lean theorem-proving benchmarks.",
    no_args_is_help=True,
)
datasets_app = typer.Typer(help="Inspect and normalize Lean benchmark datasets.")
app.add_typer(datasets_app, name="datasets")
console = Console()


class ResultReporter(Protocol):
    async def __call__(self, result: AttemptResult) -> None: ...


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


async def _run_evaluation(
    problems: list,
    settings: Settings,
    source: Path,
    progress: Progress,
    progress_task: int,
) -> tuple[Path, float, int, int]:
    writer = RunWriter(settings, dataset_source=source)

    async def report(result: AttemptResult) -> None:
        progress.update(
            progress_task,
            advance=1,
            description=f"[green]passed[/green] {result.problem_id}"
            if result.passed
            else f"[red]failed[/red] {result.problem_id}",
        )

    async with OpenAICompatibleClient(settings) as model, AxleVerifier(settings) as verifier:
        _, summary = await evaluate(
            problems,
            model,
            verifier,
            attempts=settings.eval_attempts,
            concurrency=settings.eval_concurrency,
            on_result=lambda result: _append_and_report(writer, report, result),
        )
    writer.write_summary(summary)
    return writer.directory, summary.pass_at_k, summary.solved_problems, summary.problems


async def _append_and_report(
    writer: RunWriter, report: ResultReporter, result: AttemptResult
) -> None:
    await writer.append(result)
    await report(result)


@app.command("run")
def run_evaluation(
    source: Annotated[
        Path, typer.Argument(exists=True, readable=True, help="JSONL, .lean file, or directory.")
    ],
    name: Annotated[str, typer.Option("--name", help="Dataset name.")] = "custom",
    split: Annotated[str | None, typer.Option(help="Only evaluate this split.")] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="Evaluate at most N problems.")] = None,
    attempts: Annotated[
        int | None, typer.Option(min=1, help="Attempts per problem; overrides EVAL_ATTEMPTS.")
    ] = None,
    concurrency: Annotated[
        int | None, typer.Option(min=1, help="Parallel jobs; overrides EVAL_CONCURRENCY.")
    ] = None,
    results_dir: Annotated[
        Path | None, typer.Option(help="Output root; overrides EVAL_RESULTS_DIR.")
    ] = None,
) -> None:
    """Generate remote-model proofs and verify them with AXLE."""
    try:
        settings = Settings()
        settings.require_model_name()
        problems = load_dataset(source, dataset=name.lower())
    except (DatasetError, ValueError) as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(2) from exc
    if split:
        problems = [problem for problem in problems if problem.split == split]
    if limit:
        problems = problems[:limit]
    if not problems:
        console.print("[red]No problems matched the requested filters.[/red]")
        raise typer.Exit(2)
    if attempts is not None:
        settings.eval_attempts = attempts
    if concurrency is not None:
        settings.eval_concurrency = concurrency
    if results_dir is not None:
        settings.eval_results_dir = results_dir
    total = len(problems) * settings.eval_attempts
    with Progress(console=console) as progress:
        task = progress.add_task("Starting evaluation", total=total)
        try:
            output, pass_at_k, solved, problem_count = asyncio.run(
                _run_evaluation(problems, settings, source, progress, task)
            )
        except KeyboardInterrupt as exc:
            console.print(
                "[yellow]Interrupted; completed attempts remain in the results directory.[/yellow]"
            )
            raise typer.Exit(130) from exc
    console.print(
        f"Solved [bold]{solved}/{problem_count}[/bold] problems; "
        f"pass@{settings.eval_attempts} = [bold]{pass_at_k:.2%}[/bold]"
    )
    console.print(f"Results: {output}")
