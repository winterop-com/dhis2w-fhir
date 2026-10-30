"""Step 7: mount `/metadata` and capture in your own FastAPI application.

This is the same facade behind routes you did not have to write. Declare your own routes first, because the reads
claim every one-segment path. `/metadata` states what was mounted, so a surface left out is neither served nor
declared: reads are off here and the statement says so.

Usage:
    uv run python examples/facade/step7_mount_metadata_and_capture.py

"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx2
from _connect import opened_facade
from dhis2w_fhir_engine.facade.api import attach_facade, mount_facade
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Open the facade while the service starts, and hand it to the routes."""
    async with opened_facade() as facade:
        attach_facade(app, facade)
        yield


app = FastAPI(lifespan=lifespan)


@app.get("/status")
async def status() -> dict[str, str]:
    """A route of this service's own."""
    return {"service": "mine"}


# `allow_anonymous` because this runs in-process with no port. Steps 8 and 9 are what a real service states.
mount_facade(app, reads=False, operations=False, allow_anonymous=True)


async def main() -> None:
    """Drive the application in-process: no port is bound."""
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://service") as caller:
            print("GET /status ->", (await caller.get("/status")).json())
            statement = (await caller.get("/metadata")).json()
            print("declares:", [resource["type"] for resource in statement["rest"][0]["resource"]])
            print("GET /Questionnaire ->", (await caller.get("/Questionnaire")).status_code)


if __name__ == "__main__":
    asyncio.run(main())
