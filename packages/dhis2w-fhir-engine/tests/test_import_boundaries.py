"""Tests that each part of the engine loads only what it is allowed to."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_CHECK = Path(__file__).parents[3] / "scripts" / "check_engine_boundaries.py"


def test_each_part_of_the_engine_stays_inside_its_boundary() -> None:
    """The evaluator loads no DHIS2, and nothing a library caller imports loads a server, generator, or terminal."""
    result = subprocess.run([sys.executable, str(_CHECK)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
