"""Run every non-interactive example and summarise PASS / FAIL / TIMEOUT / SKIP.

**Every example must be verified here.** Full execution coverage is the end state: an example that
this suite does not run is an example nobody knows still works. An entry that genuinely cannot run
in a batch pass earns a place in `SKIP_BY_DEFAULT` with a stated reason, and that reason is the
contract - "binds a port and compiles for minutes", "writes to the instance", not "it is slow to
look at".

Targets every script under `examples/{cli,client,engine}/`. The three packages are not per-version,
so one copy runs against whichever DHIS2 major the active profile points at.

Files starting with `_` are skipped (helper modules like `_runner.py` and `_fixture.py`). Each
example runs via `bash <path>` for `.sh` and `uv run python <path>` for `.py`, inheriting the
parent environment plus `DHIS2_PROFILE`, so profile-driven examples pick the right instance.

Two things the suite arranges before the loop, because a batch pass can afford them once where a
single example cannot:

- **One shared fixture.** Every `examples/client/` example stands up a scaffolded project and a
  `d2w fhir serve --live` facade of its own when the `D2W_FHIR_EXAMPLE_PROJECT` /
  `D2W_FHIR_EXAMPLE_FACADE` seams are unset. The suite stands one up, exports the seams, and stops
  the facade after the last example - so a pass boots one server rather than a dozen.
- **Environment-conditional skips.** An example reading a real secret or endpoint out of the
  environment runs when every variable it names is set and skips naming the missing ones
  otherwise. An unprovisioned machine is a fact about the machine, not a defect in the example.

Usage:
    uv run python scripts/verify_examples.py            # run against the local_basic profile
    uv run python scripts/verify_examples.py --list     # print what would run and what is skipped
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import BaseModel, ConfigDict
from rich.console import Console
from rich.table import Table

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_ROOT = REPOSITORY_ROOT / "examples"

# The pack is driven from the command line and from Python, plus the evaluation engine, which is
# its own package and its own kind of caller: expressions over FHIR-shaped data, with no DHIS2 in
# the picture. `examples/igs/` holds committed guides, verified by `make verify-igs`, not here.
SURFACES = ("cli", "client", "engine")

# Examples a batch pass cannot run. Paths are relative to `examples/`. Every entry states why it
# cannot be executed, and closing one of these gaps is a fix, not a nicety.
SKIP_BY_DEFAULT: frozenset[str] = frozenset(
    {
        # Scaffolds a guide, runs the dockerized SUSHI compile, then starts `d2w fhir serve` as a
        # background job and curls it. The compile alone is minutes on a cold docker image, and the
        # script binds a port - neither belongs in a batch pass.
        "cli/serve.sh",
        # The same compile and the same bound port to fill the spool the drain reads. The dry run
        # writes nothing to the instance; every other forward story commits, so
        # `d2w fhir forward --import` writes data values.
        "cli/forward_dry_run.sh",
        "cli/forward_import.sh",
        # The overwrite and completeness stories carry the same compile, the same bound port, and
        # the same committing writes as forward_import.sh.
        "cli/forward_overwrites.sh",
        "cli/forward_completeness.sh",
        # The withdrawal story binds the same port and makes two committing writes of its own: one
        # creates an event in the instance, the other deletes it.
        "cli/withdraw.sh",
        # Creates three tracked entity types, a tracked entity attribute, three registration
        # programmes, and a tracked entity apiece on the instance, then removes all of it -
        # including a `d2w maintenance cleanup tracked-entities` purge, which hard-removes every
        # soft-deleted tracked entity on the instance and not only this script's. Writes plus a
        # purge is not a batch pass.
        "cli/registers_many_types.sh",
        # `d2w fhir doctor` runs the whole chain - scaffold, generate, dockerized compile, serve,
        # capture, forward - in one command. Minutes per run, for the same compile reason.
        "cli/doctor_probe.sh",
        # Each doctor story is its own run of that whole chain, and `--all-targets` runs it over
        # every data set and every program.
        "cli/doctor_all_targets.sh",
        "cli/doctor_live_oracle.sh",
        "cli/doctor_report.sh",
        "cli/doctor_json.sh",
        # Same whole chain, and it needs a project directory holding a guide that was generated and
        # compiled at some earlier point to read.
        "cli/doctor_drift.sh",
    },
)

# Examples that read real secrets or endpoints from the environment. Each entry runs when every
# named variable is set and skips with the missing names stated otherwise.
SKIP_WHEN_ENVIRONMENT_MISSING: dict[str, tuple[str, ...]] = {
    # The one engine example that reads DHIS2: it maps a seeded Child Programme cohort into FHIR
    # and scores a measure over it. Every other engine example evaluates over inline data.
    "engine/e2e_measure_from_dhis2.py": ("DHIS2_URL", "DHIS2_USERNAME", "DHIS2_PASSWORD"),
    # The `dhis2` posture checks a caller's own DHIS2 credentials against the instance, so the
    # example presents a real one - a caller's, never the facade's profile. The personal access
    # token is the same posture with no password on the wire.
    "cli/serve_auth_postures.sh": ("DHIS2_USERNAME", "DHIS2_PASSWORD", "DHIS2_PAT"),
}

DEFAULT_PROFILE = "local_basic"
DEFAULT_TIMEOUT_SECONDS = 300.0

ExampleStatus = Literal["PASS", "FAIL", "TIMEOUT", "SKIP", "RUN"]
STATUSES: tuple[ExampleStatus, ...] = ("PASS", "FAIL", "TIMEOUT", "SKIP")


class ExampleResult(BaseModel):
    """One example run's outcome: path, surface, status and wall-clock."""

    model_config = ConfigDict(frozen=True)

    path: str
    surface: str
    status: ExampleStatus
    seconds: float
    stderr_tail: str = ""
    left_behind: tuple[str, ...] = ()


def discover_examples() -> list[Path]:
    """Every example file under the three surfaces, sorted by path, without `_` or `.` helpers."""
    paths: list[Path] = []
    for surface in SURFACES:
        directory = EXAMPLES_ROOT / surface
        if not directory.exists():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.name.startswith(("_", ".")):
                continue
            if entry.suffix in {".sh", ".py"}:
                paths.append(entry)
    return sorted(paths)


def surface_of(path: Path) -> str:
    """The summary row an example belongs under: `cli`, `client` or `engine`."""
    return path.relative_to(EXAMPLES_ROOT).parts[0]


def skip_reason(path: Path, *, skip: frozenset[str]) -> str | None:
    """Why this example is skipped in this pass, or None when it runs."""
    relative = path.relative_to(EXAMPLES_ROOT).as_posix()
    if relative in skip:
        return "skipped by default"
    missing = [name for name in SKIP_WHEN_ENVIRONMENT_MISSING.get(relative, ()) if not os.environ.get(name)]
    if missing:
        return f"environment not set: {', '.join(missing)}"
    return None


def _run_one(path: Path, *, profile: str, timeout_seconds: float) -> ExampleResult:
    """Invoke one example with the given profile and timeout, and capture its outcome."""
    surface = surface_of(path)
    relative = path.relative_to(REPOSITORY_ROOT).as_posix()
    environment = {**os.environ, "DHIS2_PROFILE": profile}
    command = ["bash", str(path)] if path.suffix == ".sh" else ["uv", "run", "python", str(path)]
    root_entries_before = {entry.name for entry in REPOSITORY_ROOT.iterdir()}
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout_seconds,
            env=environment,
            cwd=REPOSITORY_ROOT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ExampleResult(
            path=relative,
            surface=surface,
            status="TIMEOUT",
            seconds=time.monotonic() - start,
            left_behind=_sweep_root(root_entries_before),
        )
    elapsed = time.monotonic() - start
    left_behind = _sweep_root(root_entries_before)
    if completed.returncode == 0:
        return ExampleResult(path=relative, surface=surface, status="PASS", seconds=elapsed, left_behind=left_behind)
    stderr = completed.stderr.decode(errors="replace").strip()
    stdout = completed.stdout.decode(errors="replace").strip()
    tail = "\n".join((stderr or stdout).splitlines()[-6:])
    return ExampleResult(
        path=relative, surface=surface, status="FAIL", seconds=elapsed, stderr_tail=tail, left_behind=left_behind
    )


def _sweep_root(root_entries_before: set[str]) -> tuple[str, ...]:
    """Remove what an example left at the repository root and name it, so the working tree stays clean.

    Examples scaffold projects in the working directory (`d2w fhir init sync-demo`) and remove them
    on their last line, which a failure or a timeout never reaches. Everything new at the root after
    a run is the example's, never the repository's, so it is removed and reported.
    """
    left_behind: list[str] = []
    for entry in sorted(REPOSITORY_ROOT.iterdir()):
        if entry.name in root_entries_before:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        left_behind.append(entry.name)
    return tuple(left_behind)


def _stand_up_shared_fixture(examples: list[Path], skip: frozenset[str], console: Console) -> Callable[[], None] | None:
    """Stand the client examples' shared project and facade up once, for the whole suite.

    Every `examples/client/` example builds its own fixture when the two seams
    (`D2W_FHIR_EXAMPLE_PROJECT`, `D2W_FHIR_EXAMPLE_FACADE`) are unset - which in a batch pass means
    each example booting a `d2w fhir serve --live` of its own. This stands one up in this process and
    exports the seams, so every example reuses it, and hands back the call that stops the facade and
    clears the seams once the loop is done. Seams already set are an operator's own fixture and are
    left alone, and a fixture that cannot build is reported plainly - each example then builds its
    own. Either way the answer is `None`: there is nothing of this suite's to stop.

    Two postures are deliberately not shared. `served_facade(auth=...)` honours the facade seam for
    the open default posture only, so the two examples about authentication each start a guarded
    facade of their own.
    """
    wanted = any(surface_of(path) == "client" and skip_reason(path, skip=skip) is None for path in examples)
    if not wanted:
        return None
    fixture_directory = EXAMPLES_ROOT / "client"
    sys.path.insert(0, str(fixture_directory))
    try:
        import _fixture  # noqa: PLC0415

        if os.environ.get(_fixture.PROJECT_ENVIRONMENT_VARIABLE) or os.environ.get(
            _fixture.FACADE_ENVIRONMENT_VARIABLE
        ):
            return None
        project_root = _fixture.example_project()
        _fixture.conversion_context()
        facade = _fixture.served_facade()
        os.environ[_fixture.PROJECT_ENVIRONMENT_VARIABLE] = str(project_root)
        os.environ[_fixture.FACADE_ENVIRONMENT_VARIABLE] = facade
        console.print(f"shared fixture: project [cyan]{project_root}[/cyan], facade [cyan]{facade}[/cyan]")
    except Exception as error:  # noqa: BLE001 - the fallback is the point: each example builds its own
        console.print(f"[yellow]shared fixture unavailable ({error}); each example builds its own[/yellow]")
        return None
    finally:
        sys.path.remove(str(fixture_directory))

    def tear_down() -> None:
        """Stop the shared facade and clear the seams, so nothing outlives the loop that started it."""
        _fixture.stop_facades()
        os.environ.pop(_fixture.PROJECT_ENVIRONMENT_VARIABLE, None)
        os.environ.pop(_fixture.FACADE_ENVIRONMENT_VARIABLE, None)

    return tear_down


def plan_suite(*, include_skipped: bool = False) -> list[ExampleResult]:
    """What a pass would do, without running anything: `RUN` for each example it would run, `SKIP` with a reason."""
    skip = frozenset() if include_skipped else SKIP_BY_DEFAULT
    plan: list[ExampleResult] = []
    for path in discover_examples():
        reason = skip_reason(path, skip=skip)
        plan.append(
            ExampleResult(
                path=path.relative_to(REPOSITORY_ROOT).as_posix(),
                surface=surface_of(path),
                status="SKIP" if reason else "RUN",
                seconds=0.0,
                stderr_tail=reason or "",
            )
        )
    return plan


def run_suite(
    *,
    profile: str = DEFAULT_PROFILE,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    include_skipped: bool = False,
    console: Console | None = None,
) -> list[ExampleResult]:
    """Run every discovered example, stream one status line each, and return every outcome."""
    os.environ["DHIS2_PROFILE"] = profile
    skip = frozenset() if include_skipped else SKIP_BY_DEFAULT
    console = console or Console()
    examples = discover_examples()
    console.print(
        f"running [bold]{len(examples)}[/bold] examples (profile=[cyan]{profile}[/cyan], "
        f"timeout={int(timeout_seconds)}s, skip-default={'off' if include_skipped else 'on'})"
    )
    tear_down = _stand_up_shared_fixture(examples, skip, console)
    results: list[ExampleResult] = []
    try:
        for path in examples:
            reason = skip_reason(path, skip=skip)
            if reason is not None:
                result = ExampleResult(
                    path=path.relative_to(REPOSITORY_ROOT).as_posix(),
                    surface=surface_of(path),
                    status="SKIP",
                    seconds=0.0,
                    stderr_tail=reason,
                )
            else:
                result = _run_one(path, profile=profile, timeout_seconds=timeout_seconds)
            _print_result(result, console)
            results.append(result)
    finally:
        if tear_down is not None:
            tear_down()
    return results


def _print_result(result: ExampleResult, console: Console) -> None:
    """One status line for one example, plus anything it left at the repository root."""
    colour = {"PASS": "green", "FAIL": "red", "TIMEOUT": "yellow", "SKIP": "dim", "RUN": "cyan"}[result.status]
    reason = f"  [dim]({result.stderr_tail})[/dim]" if result.status in {"SKIP", "RUN"} and result.stderr_tail else ""
    console.print(f"  [{colour}]{result.status:8s}[/{colour}] {result.seconds:6.2f}s  {result.path}{reason}")
    if result.left_behind:
        console.print(
            f"           [yellow]left at the repository root and removed: {', '.join(result.left_behind)}[/yellow]"
        )


def render_summary(results: list[ExampleResult], *, console: Console | None = None) -> int:
    """Print a per-surface summary table and every failure's tail. Return 0 when nothing failed."""
    console = console or Console()
    counts: Counter[tuple[str, str]] = Counter((result.surface, result.status) for result in results)
    surfaces = sorted({result.surface for result in results})
    table = Table(title=f"example verification summary ({len(results)} total)")
    table.add_column("surface", style="cyan")
    for status, style in zip(STATUSES, ("green", "red", "yellow", "dim"), strict=True):
        table.add_column(status.lower(), justify="right", style=style)
    for surface in surfaces:
        table.add_row(surface, *(str(counts[(surface, status)]) for status in STATUSES))
    table.add_row(
        "TOTAL",
        *(str(sum(counts[(surface, status)] for surface in surfaces)) for status in STATUSES),
        style="bold",
    )
    console.print(table)
    failures = [result for result in results if result.status in {"FAIL", "TIMEOUT"}]
    if failures:
        console.print(f"\n[red bold]{len(failures)} failure(s)[/red bold]:")
        for result in failures:
            console.print(f"  [red]{result.status:8s}[/red] {result.path}")
            for line in result.stderr_tail.splitlines():
                console.print(f"    [dim]{line}[/dim]")
        return 1
    console.print("[green bold]all green[/green bold]")
    return 0


application = typer.Typer(add_completion=False, help="Run every non-interactive example and summarise the outcome.")


@application.command()
def main(
    profile: Annotated[str, typer.Option(help="DHIS2_PROFILE each example runs against.")] = DEFAULT_PROFILE,
    timeout: Annotated[float, typer.Option(help="Per-example timeout in seconds.")] = DEFAULT_TIMEOUT_SECONDS,
    include_skipped: Annotated[
        bool, typer.Option("--include-skipped", help="Also run the examples skipped by default.")
    ] = False,
    list_only: Annotated[
        bool, typer.Option("--list", help="Print what a pass would run and skip, and run nothing.")
    ] = False,
) -> None:
    """Run the suite (or list it) and exit non-zero when an example failed."""
    console = Console()
    if list_only:
        plan = plan_suite(include_skipped=include_skipped)
        for result in plan:
            _print_result(result, console)
        by_surface = Counter((result.surface, result.status) for result in plan)
        for surface in SURFACES:
            console.print(
                f"{surface}: {by_surface[(surface, 'RUN')]} run, {by_surface[(surface, 'SKIP')]} skipped",
            )
        return
    results = run_suite(profile=profile, timeout_seconds=timeout, include_skipped=include_skipped, console=console)
    raise typer.Exit(render_summary(results, console=console))


if __name__ == "__main__":
    application()
