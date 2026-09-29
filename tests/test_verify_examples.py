"""Unit tests for `scripts/verify_examples.py` and `scripts/check_example_refs.py`."""

from __future__ import annotations

import sys
from pathlib import Path

import click
import pytest
from pydantic import ValidationError
from rich.console import Console

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from check_example_refs import _bad_cli_refs, _command_tree  # noqa: E402 - path-prepend intentional
from verify_examples import (  # noqa: E402 - path-prepend intentional
    EXAMPLES_ROOT,
    SKIP_BY_DEFAULT,
    SKIP_WHEN_ENVIRONMENT_MISSING,
    SURFACES,
    ExampleResult,
    discover_examples,
    plan_suite,
    render_summary,
)


def test_discover_examples_covers_every_surface_and_drops_helpers() -> None:
    paths = discover_examples()
    assert {path.relative_to(EXAMPLES_ROOT).parts[0] for path in paths} == set(SURFACES)
    assert all(not path.name.startswith("_") for path in paths)
    assert all(path.suffix in {".sh", ".py"} for path in paths)


def test_discovery_leaves_the_committed_guides_to_verify_igs() -> None:
    assert not any(path.relative_to(EXAMPLES_ROOT).parts[0] == "igs" for path in discover_examples())


def test_skip_list_covers_the_compile_and_write_stories() -> None:
    assert "cli/serve.sh" in SKIP_BY_DEFAULT
    assert "cli/forward_import.sh" in SKIP_BY_DEFAULT
    assert "cli/doctor_probe.sh" in SKIP_BY_DEFAULT


def test_skip_entries_name_files_that_exist() -> None:
    missing = sorted(entry for entry in SKIP_BY_DEFAULT if not (EXAMPLES_ROOT / entry).exists())
    assert not missing, f"skip entries with no file: {missing}"


def test_environment_skip_entries_name_files_that_exist() -> None:
    missing = sorted(entry for entry in SKIP_WHEN_ENVIRONMENT_MISSING if not (EXAMPLES_ROOT / entry).exists())
    assert not missing, f"environment-skip entries with no file: {missing}"
    assert all(SKIP_WHEN_ENVIRONMENT_MISSING.values())


def test_plan_runs_every_client_example_and_skips_the_default_list(monkeypatch: pytest.MonkeyPatch) -> None:
    for names in SKIP_WHEN_ENVIRONMENT_MISSING.values():
        for name in names:
            monkeypatch.setenv(name, "set")
    plan = plan_suite()
    skipped = {result.path.removeprefix("examples/") for result in plan if result.status == "SKIP"}
    assert skipped == set(SKIP_BY_DEFAULT)
    assert all(result.status == "RUN" for result in plan if result.surface == "client")


def test_plan_skips_an_example_whose_environment_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SKIP_WHEN_ENVIRONMENT_MISSING["engine/e2e_measure_from_dhis2.py"]:
        monkeypatch.delenv(name, raising=False)
    entry = next(result for result in plan_suite() if result.path == "examples/engine/e2e_measure_from_dhis2.py")
    assert entry.status == "SKIP"
    assert "DHIS2_URL" in entry.stderr_tail


def test_render_summary_returns_zero_when_all_pass(capsys: pytest.CaptureFixture[str]) -> None:
    results = [
        ExampleResult(path="examples/cli/init.sh", surface="cli", status="PASS", seconds=1.0),
        ExampleResult(path="examples/engine/fhirpath_basics.py", surface="engine", status="SKIP", seconds=0.0),
    ]
    assert render_summary(results, console=Console(force_terminal=False, width=120)) == 0
    output = capsys.readouterr().out
    assert "all green" in output
    assert "TOTAL" in output


def test_render_summary_returns_one_and_prints_tails_on_failure(capsys: pytest.CaptureFixture[str]) -> None:
    results = [
        ExampleResult(path="examples/cli/init.sh", surface="cli", status="PASS", seconds=1.0),
        ExampleResult(
            path="examples/cli/validate.sh", surface="cli", status="FAIL", seconds=2.0, stderr_tail="ERR: boom"
        ),
        ExampleResult(path="examples/client/basic_facade.py", surface="client", status="TIMEOUT", seconds=300.0),
    ]
    assert render_summary(results, console=Console(force_terminal=False, width=120)) == 1
    output = capsys.readouterr().out
    assert "2 failure" in output
    assert "ERR: boom" in output


def test_example_result_is_frozen() -> None:
    result = ExampleResult(path="x", surface="cli", status="PASS", seconds=1.0)
    with pytest.raises(ValidationError):
        result.status = "FAIL"


def test_check_example_refs_flags_an_unknown_fhir_subcommand() -> None:
    root = _command_tree()
    assert _bad_cli_refs("d2w fhir generate --help\n", root) == set()
    assert _bad_cli_refs("d2w fhir no-such-command x\n", root) == {"d2w fhir no-such-command"}


def test_check_example_refs_reads_the_real_d2w_tree() -> None:
    root = _command_tree()
    assert root.get_command(click.Context(root), "fhir") is not None
