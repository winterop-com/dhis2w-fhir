"""Step 8: compose everything, behind your own authentication.

Everything is mounted here, including the operational API under a prefix this service chose. Your dependency guards
it all but `/metadata`, because a client has to be able to read the posture it is expected to meet, and a refusal
still answers as an `OperationOutcome`, whoever raised it.

Your dependency decides who gets in, not what they can read: everyone it lets through reads DHIS2 as the one account
behind the connection, which is why a real service gives that connection a dedicated account rather than `admin`.

Usage:
    uv run python examples/facade/step8_compose_with_authentication.py

"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx2
from _connect import opened_facade
from dhis2w_fhir_engine.facade.api import attach_facade, mount_facade
from fastapi import FastAPI, HTTPException, Request

UNAUTHENTICATED = 401
KEY_HEADER = "x-example-key"
KEY = {KEY_HEADER: "the-application-decides"}


def require_key(request: Request) -> None:
    """This service's own idea of a credential, standing in for whatever a real one checks."""
    if request.headers.get(KEY_HEADER) != KEY[KEY_HEADER]:
        raise HTTPException(status_code=UNAUTHENTICATED, detail="this service needs its own key")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Open the facade while the service starts, and hand it to the routes."""
    async with opened_facade() as facade:
        attach_facade(app, facade)
        yield


app = FastAPI(lifespan=lifespan)
mount_facade(app, facade_api="/controls", authentication=require_key)


async def main() -> None:
    """Drive the application in-process, with and without the key."""
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://service") as caller:
            print("GET /metadata, no key ->", (await caller.get("/metadata")).status_code)
            refused = await caller.get("/Questionnaire")
            print("GET /Questionnaire, no key ->", refused.status_code, refused.json()["resourceType"])
            print("GET /Questionnaire, key ->", (await caller.get("/Questionnaire", headers=KEY)).status_code)
            print("GET /controls/spool, key ->", (await caller.get("/controls/spool", headers=KEY)).status_code)


if __name__ == "__main__":
    asyncio.run(main())
