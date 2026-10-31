"""Tests that a facade composes into an application the way the engine promises: what is mounted, and only that."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
import respx
from dhis2w_client import BasicAuth, Dhis2Client
from dhis2w_fhir_engine.facade import assembly as assembly_module
from dhis2w_fhir_engine.facade import open_facade
from dhis2w_fhir_engine.facade.api import AnonymousFacadeError, attach_facade, mount_facade, record_caller
from dhis2w_fhir_engine.facade.config import CaptureDelivery, FhirProject
from fastapi import FastAPI, HTTPException, Request
from fixture_project import AGGREGATE_RESPONSE_FILE, REGISTRATION_TRACKED_ENTITY_TYPE_UID, golden

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

    # Each test states the posture it is about; the rest are about something else, in-process, with no port.
    if "authentication" not in surfaces and "read_as_caller" not in surfaces:
        surfaces.setdefault("allow_anonymous", True)
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
        assert created.status_code == 202


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


async def test_your_own_authentication_is_what_metadata_describes(capture_project: FhirProject) -> None:
    """A deployment's own check is not the `token` posture, and the statement does not call it one."""
    application = _application(
        capture_project, authentication=lambda: None, authentication_statement="Ask us for a key."
    )
    async with _serving(application) as client:
        security = (await client.get("/metadata")).json()["rest"][0]["security"]
    assert security["description"] == "Ask us for a key."


async def test_whoami_names_the_caller_your_check_recorded(capture_project: FhirProject) -> None:
    """A check that names its caller gets `/whoami`, and one that does not is told so rather than told it failed."""

    def names_them(request: Request) -> None:
        record_caller(request, "aminata")

    named = _application(capture_project, authentication=names_them, facade_api="/controls")
    async with _serving(named) as client:
        assert (await client.get("/controls/whoami")).json()["username"] == "aminata"

    silent = _application(capture_project, authentication=lambda: None, facade_api="/controls")
    async with _serving(silent) as client:
        answer = await client.get("/controls/whoami")
    assert answer.status_code == 404
    assert "record_caller" in answer.json()["issue"][0]["diagnostics"]


_INSTANCE = "http://dhis2.invalid"
_PERSON = "PLoWmEuLJl2"
_CALLER = f"Basic {base64.b64encode(b'clerk:secret').decode('ascii')}"


def _holding_a_client(project: FhirProject, monkeypatch: pytest.MonkeyPatch, **surfaces: Any) -> FastAPI:
    """One service whose facade holds a real DHIS2 connection, as every live facade does."""

    async def no_value_types(client: object, bound: object, *, progress: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(assembly_module, "fetch_value_types", no_value_types)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        client = Dhis2Client(base_url=_INSTANCE, auth=BasicAuth(username="facade", password="its-own"))
        async with open_facade(project, client=client) as facade:
            attach_facade(application, facade)
            yield

    application = FastAPI(lifespan=lifespan)
    mount_facade(application, **surfaces)
    return application


async def test_a_facade_holding_a_client_will_not_start_open_to_anyone(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing else stops this service being reached from another host, so it refuses to hand people to anyone."""
    with pytest.raises(AnonymousFacadeError, match="read_as_caller=True"):
        async with _serving(_holding_a_client(capture_project, monkeypatch)):
            pass


@pytest.mark.parametrize(
    "surfaces",
    [
        pytest.param({"allow_anonymous": True}, id="stated-outright"),
        pytest.param({"authentication": lambda: None}, id="your-own-check"),
        pytest.param({"read_as_caller": True}, id="dhis2-is-the-check"),
        pytest.param({"capture": False, "reads": False, "operations": False}, id="only-metadata"),
    ],
)
async def test_each_way_of_saying_what_was_meant_starts(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch, surfaces: dict[str, Any]
) -> None:
    """Every way of saying what was meant starts, including mounting nothing that needs saying."""
    async with _serving(_holding_a_client(capture_project, monkeypatch, **surfaces)) as client:
        assert (await client.get("/metadata")).status_code == 200


async def test_the_operational_api_counts_as_reading_people(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It lists tracked entities, so naming a prefix for it is not a way round the refusal."""
    application = _holding_a_client(capture_project, monkeypatch, reads=False, operations=False, facade_api="/controls")
    with pytest.raises(AnonymousFacadeError):
        async with _serving(application):
            pass


async def test_a_facade_with_no_client_has_no_people_to_hand_out(capture_project: FhirProject) -> None:
    """A guide read off disk holds forms and terminology, so serving it to anyone is not the risk."""
    application = _application(capture_project, capture=False, allow_anonymous=False)
    async with _serving(application) as client:
        assert (await client.get("/Questionnaire")).status_code == 200


@pytest.mark.parametrize("with_a_client", [True, False], ids=["holding-a-client", "no-client"])
async def test_a_facade_that_takes_captures_will_not_start_with_nobody_checking(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch, with_a_client: bool
) -> None:
    """Receiving only is still writing: whatever anyone sends is later posted as the service's account."""
    surfaces: dict[str, Any] = {"reads": False, "operations": False, "allow_anonymous": False}
    application = (
        _holding_a_client(capture_project, monkeypatch, **surfaces)
        if with_a_client
        else _application(capture_project, **surfaces)
    )
    with pytest.raises(AnonymousFacadeError):
        async with _serving(application):
            pass


def test_read_as_caller_takes_no_check_of_your_own() -> None:
    """DHIS2 is the check, so a second one would decide nothing it does not decide."""
    with pytest.raises(ValueError, match="read_as_caller"):
        mount_facade(FastAPI(), read_as_caller=True, authentication=lambda: None)


async def test_read_as_caller_with_no_client_refuses(capture_project: FhirProject) -> None:
    """There is no DHIS2 to read as anybody."""
    with pytest.raises(ValueError, match="no client"):
        async with _serving(_application(capture_project, read_as_caller=True)):
            pass


async def test_read_as_caller_sends_the_caller_s_own_credentials_and_never_the_facade_s(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is the whole point: DHIS2 sees the person who asked, so DHIS2 applies that person's rights."""
    application = _holding_a_client(capture_project, monkeypatch, read_as_caller=True)
    with respx.mock:
        respx.get(f"{_INSTANCE}/api/me").mock(return_value=httpx.Response(200, json={"username": "clerk"}))
        read = respx.get(f"{_INSTANCE}/api/tracker/trackedEntities/{_PERSON}").mock(
            return_value=httpx.Response(
                200, json={"trackedEntity": _PERSON, "trackedEntityType": REGISTRATION_TRACKED_ENTITY_TYPE_UID}
            )
        )
        async with _serving(application) as client:
            refused = await client.get(f"/Patient/{_PERSON}")
            await client.get(f"/Patient/{_PERSON}", headers={"Authorization": _CALLER})
            statement = (await client.get("/metadata")).json()
    assert refused.status_code == 401
    assert read.calls.last.request.headers["authorization"] == _CALLER
    assert "DHIS2" in statement["rest"][0]["security"]["description"]


_ACCEPTED = {
    "httpStatus": "OK",
    "status": "OK",
    "response": {"responseType": "ImportSummary", "status": "SUCCESS", "importCount": {"imported": 2, "updated": 0}},
}
_REFUSED = {
    "httpStatus": "Conflict",
    "status": "ERROR",
    "response": {
        "responseType": "ImportSummary",
        "status": "ERROR",
        "importCount": {"imported": 0, "updated": 0, "ignored": 2},
        "conflicts": [{"errorCode": "E8010", "object": "OrgUnit", "value": "User has no write access"}],
    },
}


def _capture_body() -> str:
    """One aggregate capture the fixture guide accepts."""
    return json.dumps(golden(AGGREGATE_RESPONSE_FILE))


async def _capture_as_the_clerk(client: httpx2.AsyncClient) -> httpx2.Response:
    """Submit that capture under the clerk's own header."""
    return await client.post(
        "/QuestionnaireResponse",
        content=_capture_body(),
        headers={"content-type": _FHIR_JSON, "Authorization": _CALLER},
    )


def _receipts_on_disk(project: FhirProject) -> list[str]:
    """Every receipt the spool holds, in any state, as a path under the spool."""
    root = project.project_root / ".serve" / "responses"
    return sorted(str(path.relative_to(root)) for path in root.rglob("*.json") if path.is_file())


async def test_a_capture_as_the_caller_posts_under_their_credentials_and_files_what_dhis2_took(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DHIS2 applies the clerk's rights and names the clerk, and the receipt says forwarded, not queued."""
    application = _holding_a_client(capture_project, monkeypatch, read_as_caller=True)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{_INSTANCE}/api/me").mock(return_value=httpx.Response(200, json={"username": "clerk"}))
        posted = router.post(f"{_INSTANCE}/api/dataValueSets").mock(return_value=httpx.Response(200, json=_ACCEPTED))
        registered = router.post(f"{_INSTANCE}/api/completeDataSetRegistrations").mock(
            return_value=httpx.Response(200, json=_ACCEPTED)
        )
        async with _serving(application) as client:
            answer = await _capture_as_the_clerk(client)
            receipt = await client.get(answer.headers["location"], headers={"Authorization": _CALLER})
    assert answer.status_code == 201
    assert receipt.status_code == 200
    assert posted.calls.last.request.headers["authorization"] == _CALLER
    if registered.called:
        assert registered.calls.last.request.headers["authorization"] == _CALLER
    on_disk = _receipts_on_disk(capture_project)
    assert on_disk and all(path.startswith("forwarded/") for path in on_disk)


async def test_a_capture_dhis2_refuses_the_caller_is_refused_and_nothing_is_stored(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clerk cannot have the service write where DHIS2 would refuse the clerk."""
    application = _holding_a_client(capture_project, monkeypatch, read_as_caller=True)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{_INSTANCE}/api/me").mock(return_value=httpx.Response(200, json={"username": "clerk"}))
        router.post(f"{_INSTANCE}/api/dataValueSets").mock(return_value=httpx.Response(409, json=_REFUSED))
        async with _serving(application) as client:
            answer = await _capture_as_the_clerk(client)
    assert answer.status_code == 409
    assert answer.json()["resourceType"] == "OperationOutcome"
    assert "E8010" in answer.json()["issue"][0]["diagnostics"]
    assert _receipts_on_disk(capture_project) == []


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx.Response(500, json={"status": "ERROR"}), id="dhis2-failing"),
        pytest.param(httpx2.ConnectError("no route"), id="dhis2-away"),
    ],
)
async def test_dhis2_away_is_a_failure_the_caller_hears_and_nothing_is_stored(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch, failure: Any
) -> None:
    """There is no spool in this mode, so saying 201 here would be losing the capture quietly."""
    application = _holding_a_client(capture_project, monkeypatch, read_as_caller=True)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{_INSTANCE}/api/me").mock(return_value=httpx.Response(200, json={"username": "clerk"}))
        router.post(f"{_INSTANCE}/api/dataValueSets").mock(side_effect=[failure])
        async with _serving(application) as client:
            answer = await _capture_as_the_clerk(client)
    assert answer.status_code == 502
    assert "send it again" in answer.json()["issue"][0]["diagnostics"]
    assert _receipts_on_disk(capture_project) == []


async def test_a_capture_as_the_caller_needs_a_credential(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing reaches DHIS2 for a caller nobody knows."""
    application = _holding_a_client(capture_project, monkeypatch, read_as_caller=True)
    async with _serving(application) as client:
        answer = await client.post(
            "/QuestionnaireResponse", content=_capture_body(), headers={"content-type": _FHIR_JSON}
        )
    assert answer.status_code == 401
    assert _receipts_on_disk(capture_project) == []


async def test_validate_only_asks_dhis2_and_writes_nothing(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DHIS2's own rules grade the caller's capture, under its validate-only mode, and no receipt is filed."""
    application = _holding_a_client(
        capture_project, monkeypatch, read_as_caller=True, delivery=CaptureDelivery.VALIDATE_ONLY
    )
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{_INSTANCE}/api/me").mock(return_value=httpx.Response(200, json={"username": "clerk"}))
        posted = router.post(f"{_INSTANCE}/api/dataValueSets").mock(return_value=httpx.Response(200, json=_ACCEPTED))
        registered = router.post(f"{_INSTANCE}/api/completeDataSetRegistrations")
        async with _serving(application) as client:
            answer = await _capture_as_the_clerk(client)
    assert answer.status_code == 200
    assert posted.calls.last.request.url.params["dryRun"] == "true"
    assert not registered.called
    assert _receipts_on_disk(capture_project) == []
