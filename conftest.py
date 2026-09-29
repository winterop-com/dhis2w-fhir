"""Root pytest configuration: load the test environment dhis2w-core ships as a pytest plugin."""

from __future__ import annotations

pytest_plugins = ["dhis2w_core.testing"]
