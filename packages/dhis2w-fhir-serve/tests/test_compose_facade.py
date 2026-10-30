"""Tests that a facade composes into an application the way the engine promises: what is mounted, and only that."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from dhis2w_fhir_engine.facade import open_facade
from dhis2w_fhir_engine.facade.api import attach_facade, mount_facade
from dhis2w_fhir_engine.facade.config import FhirProject
from fastapi import FastAPI, HTTPException, Request
from fixture_project import AGGREGATE_RESPONSE_FILE, golden

_FHIR_JSON = "application/fhir+json"


def _application(project: FhirProject, *, service_base: str | None = None, **surfaces: Any) -> FastAPI:
    """One service that declares its own route, then mounts what the test names."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        async with open_facade(project) as facade:
            attach_facade(application, facade, service_base=service_base)
            yield

    application = FastAPI(lifespan=lifespan)

    @application.get("/status")
    async def status() -> dict[str, str]:
        return {"service": "mine"}

    mount_facade(application, **surfaces)
    return application


@asynccontextmanager
async def _serving(application: FastAPI) -> AsyncGenerator[httpx2.AsyncClient]:
    """Run the application's startup and talk to it with no socket."""
    async with application.router.lifespan_context(application):
        transport = httpx2.ASGITransport(app=application)
        async with httpx2.AsyncClient(transport=transport, base_url="http://service") as client:
            yield client


async def test_everything_mounted_answers_beside_the_application_s_own_route(capture_project: FhirProject) -> None:
    """The reads are catch-alls, and a route declared first keeps its address."""
    async with _serving(_application(capture_project)) as client:
        assert (await client.get("/status")).json() == {"service": "mine"}
        assert (await client.get("/metadata")).status_code == 200
        assert (await client.get("/Questionnaire")).json()["resourceType"] == "Bundle"
        assert (await client.head("/metadata")).status_code == 200


async def test_metadata_states_only_what_was_mounted(capture_project: FhirProject) -> None:
    """A surface left out is neither served nor declared."""
    application = _application(capture_project, reads=False, operations=False)
    async with _serving(application) as client:
        statement = (await client.get("/metadata")).json()
        rest = statement["rest"][0]
        assert {resource["type"] for resource in rest["resource"]} == {"QuestionnaireResponse"}
        assert "operation" not in rest
        assert (await client.get("/Questionnaire")).status_code == 404
        created = await client.post(
            "/QuestionnaireResponse",
            content=json.dumps(golden(AGGREGATE_RESPONSE_FILE)),
            headers={"content-type": _FHIR_JSON},
        )
        assert created.status_code == 201


async def test_capture_left_out_with_reads_mounted_refuses_with_the_reason(capture_project: FhirProject) -> None:
    """The address never falls through to the read catch-all."""
    async with _serving(_application(capture_project, capture=False)) as client:
        answer = await client.post("/QuestionnaireResponse", content="{}", headers={"content-type": _FHIR_JSON})
        assert answer.status_code != 201
        assert answer.json()["resourceType"] == "OperationOutcome"


async def test_your_own_authentication_guards_everything_but_metadata(capture_project: FhirProject) -> None:
    """A client has to be able to read the posture it is expected to meet."""

    def require_key(request: Request) -> None:
        if request.headers.get("x-key") != "yes":
            raise HTTPException(status_code=401, detail="this service needs its own key")

    async with _serving(_application(capture_project, authentication=require_key)) as client:
        assert (await client.get("/metadata")).status_code == 200
        refused = await client.get("/Questionnaire")
        assert refused.status_code == 401
        assert refused.json()["resourceType"] == "OperationOutcome"
        assert (await client.get("/Questionnaire", headers={"x-key": "yes"})).status_code == 200


async def test_a_stated_service_base_is_what_links_are_built_from(capture_project: FhirProject) -> None:
    """Under a router prefix the request cannot say where the facade is, so the application does."""
    application = _application(capture_project, service_base="https://example.org/fhir")
    async with _serving(application) as client:
        bundle = (await client.get("/Questionnaire")).json()
    assert bundle["entry"][0]["fullUrl"].startswith("https://example.org/fhir/")


async def test_the_operational_api_is_left_out_unless_a_prefix_is_named(capture_project: FhirProject) -> None:
    """A service operating itself exposes no receipts listing by accident."""
    async with _serving(_application(capture_project)) as client:
        assert (await client.get("/facade/spool")).status_code == 404
    async with _serving(_application(capture_project, facade_api="/controls")) as client:
        assert (await client.get("/controls/spool")).status_code == 200


async def test_the_operation_metadata_declares_is_one_the_facade_serves(capture_project: FhirProject) -> None:
    """A definition `/metadata` names and nothing answers would send a client to a 404."""
    async with _serving(_application(capture_project)) as client:
        statement = (await client.get("/metadata")).json()
        definition = statement["rest"][0]["operation"][0]["definition"]
        served = await client.get(f"/OperationDefinition/{definition.rsplit('/', 1)[-1]}")
    assert served.status_code == 200
    assert served.json()["resourceType"] == "OperationDefinition"
