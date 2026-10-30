"""Where every step of this path finds its DHIS2: the environment, or the local demo stack."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from dhis2w_client import BasicAuth, Dhis2Client
from dhis2w_fhir_engine.facade import Facade, FacadeConfig, open_facade

CANONICAL = "http://example.org/fhir/demo"


def client() -> Dhis2Client:
    """A connection to the DHIS2 the environment names, not yet opened."""
    return Dhis2Client(
        base_url=os.environ.get("DHIS2_URL", "http://localhost:8080"),
        auth=BasicAuth(
            username=os.environ.get("DHIS2_USERNAME", "admin"),
            password=os.environ.get("DHIS2_PASSWORD", "district"),
        ),
    )


@asynccontextmanager
async def opened_facade() -> AsyncGenerator[Facade]:
    """One facade with its files in a directory that goes away afterwards, which is step 2 written once."""
    with tempfile.TemporaryDirectory() as root:
        async with (
            client() as connection,
            open_facade(FacadeConfig(canonical=CANONICAL, root=Path(root)), client=connection) as facade,
        ):
            yield facade
