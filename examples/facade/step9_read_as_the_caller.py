"""Step 9: have DHIS2 apply each caller's own rights.

With your own check, step 8, everyone who gets in acts as the one account behind the connection. `read_as_caller` is the
other answer: each caller presents their own DHIS2 credentials, DHIS2 checks them, and everything done for that caller
goes out under them. A clerk reads only who that clerk may see, and a capture they may not make is refused. A caller
with no credential is refused too, and `/metadata` stays open because a client has to be able to read the posture it is
expected to meet.

A capture posts at once rather than being spooled, so DHIS2 names the clerk as its author, and if DHIS2 is away the
capture fails and the client sends it again. `VALIDATE_ONLY` is here so this example writes nothing: a real service
leaves it out. The connection you give `open_facade` only builds the guide at startup, so it needs to read metadata.

Usage:
    uv run python examples/facade/step9_read_as_the_caller.py
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx2
from _connect import opened_facade
from dhis2w_fhir_engine.facade import CaptureDelivery
from dhis2w_fhir_engine.facade.api import attach_facade, mount_facade
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Open the facade while the service starts, and hand it to the routes."""
    async with opened_facade() as facade:
        attach_facade(app, facade)
        yield


app = FastAPI(lifespan=lifespan)
mount_facade(app, read_as_caller=True, delivery=CaptureDelivery.VALIDATE_ONLY)


async def main() -> None:
    """Ask for people with no credential, then as one DHIS2 user."""
    caller = httpx2.BasicAuth(
        os.environ.get("DHIS2_USERNAME", "admin"),
        os.environ.get("DHIS2_PASSWORD", "district"),
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://service") as anyone:
            print("GET /metadata, no credential ->", (await anyone.get("/metadata")).status_code)
            print("GET /Patient, no credential ->", (await anyone.get("/Patient")).status_code)
            answered = await anyone.get("/Patient", params={"_count": 1}, auth=caller)
            print("GET /Patient, as a DHIS2 user ->", answered.status_code, answered.json()["resourceType"])

            forms = (await anyone.get("/Questionnaire", params={"_count": 1}, auth=caller)).json()
            form = forms["entry"][0]["resource"]["id"]
            draft = await anyone.get(f"/Questionnaire/{form}/$generate", params={"seed": 1}, auth=caller)
            headers = {"content-type": "application/fhir+json"}
            taken = await anyone.post("/QuestionnaireResponse", content=draft.content, headers=headers, auth=caller)
            print(
                "POST /QuestionnaireResponse, as a DHIS2 user ->",
                taken.status_code,
                taken.json()["issue"][0]["diagnostics"],
            )


if __name__ == "__main__":
    asyncio.run(main())
