"""The `d2w-fhir-engine` console script, which names the install when the CLI's extra is missing."""

from __future__ import annotations

import sys

#: The two distributions only the command line needs, which the `[cli]` extra brings.
_CLI_ONLY_DISTRIBUTIONS = frozenset({"rich", "typer"})


def main() -> None:
    """Run the command line, or say how to install it."""
    try:
        from dhis2w_fhir_engine.cli import app
    except ModuleNotFoundError as error:
        if error.name not in _CLI_ONLY_DISTRIBUTIONS:
            raise
        sys.exit("d2w-fhir-engine needs its command line extra: uv add 'dhis2w-fhir-engine[cli]'")
    app()
