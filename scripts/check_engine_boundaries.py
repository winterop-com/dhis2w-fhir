"""Check what each part of dhis2w_fhir_engine loads when imported, in a fresh interpreter per part."""

from __future__ import annotations

import subprocess
import sys

#: What no part of the engine a library caller imports may load: the command line, the generator, the server.
NEVER = (
    "dhis2w_core",
    "dhis2w_fhir",
    "dhis2w_fhir_serve",
    "fpdf",
    "jinja2",
    "joserfc",
    "rich",
    "sqlalchemy",
    "typer",
    "uvicorn",
)

#: What the facade's logic may not load, so a batch job pays for no web framework.
WEB = ("fastapi", "starlette")

#: Each part, and everything it may not load. The evaluator is free of DHIS2 as code.
PARTS = {
    "dhis2w_fhir_engine.evaluation": (*NEVER, *WEB, "dhis2w_client"),
    "dhis2w_fhir_engine.r4": (*NEVER, *WEB, "dhis2w_client"),
    "dhis2w_fhir_engine.facade": (*NEVER, *WEB),
}

_PROBE = """
import importlib, pkgutil, sys
root = importlib.import_module(sys.argv[1])
for found in pkgutil.walk_packages(root.__path__, root.__name__ + "."):
    if ".api" not in found.name:
        importlib.import_module(found.name)
forbidden = sys.argv[2:]
loaded = sorted(name for name in forbidden if name in sys.modules)
if loaded:
    sys.exit(f"{root.__name__} loads {loaded}")
"""


def main() -> int:
    """Probe every part, printing each verdict; answer the exit status."""
    failures = 0
    for part, forbidden in PARTS.items():
        result = subprocess.run([sys.executable, "-c", _PROBE, part, *forbidden], capture_output=True, text=True)
        if result.returncode:
            failures += 1
            print(f"[x] {part}: {result.stderr.strip().splitlines()[-1]}")
        else:
            print(f"[ok] {part}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
