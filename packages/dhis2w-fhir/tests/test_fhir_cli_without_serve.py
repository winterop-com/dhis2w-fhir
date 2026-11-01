"""The `d2w fhir` CLI imports without the `dhis2w-fhir-serve` package, which only the `[serve]` extra installs."""

from __future__ import annotations

import subprocess
import sys

_IMPORT_WITHOUT_SERVE = """
import sys

sys.modules["dhis2w_fhir_serve"] = None
import dhis2w_fhir.cli
import dhis2w_fhir.plugin
"""


def test_cli_imports_without_the_serve_package() -> None:
    """Importing the plugin's CLI never reaches `dhis2w_fhir_serve`, so `d2w` starts on a `[fhir]`-only install."""
    completed = subprocess.run(
        [sys.executable, "-c", _IMPORT_WITHOUT_SERVE],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
