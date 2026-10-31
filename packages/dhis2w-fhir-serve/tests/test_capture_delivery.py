"""Tests that a capture reaches DHIS2 at once, that its submitter hears the answer, and that nothing sits unnoticed."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
import respx
from dhis2w_client import BasicAuth, Dhis2Client
from dhis2w_core.profile import Profile, ResolvedProfile
from dhis2w_fhir.service import GenerationProfile, resolve_generation_profile
from dhis2w_fhir_engine.facade import CaptureDeliverer, CaptureDelivery, DeliveryKind, open_facade
from dhis2w_fhir_engine.facade import assembly as assembly_module
from dhis2w_fhir_engine.facade import forward as forward_module
from dhis2w_fhir_engine.facade.api import attach_facade, mount_facade
from dhis2w_fhir_engine.facade.capability import (
    CAPTURE_AS_CALLER_REST_DOCUMENTATION,
    CAPTURE_AT_ONCE_REST_DOCUMENTATION,
    CAPTURE_HELD_REST_DOCUMENTATION,
    CAPTURE_VALIDATED_REST_DOCUMENTATION,
    _rest_documentation,
)
from dhis2w_fhir_engine.facade.capture.index import CaptureIndexCache
from dhis2w_fhir_engine.facade.capture.naming import CaptureNaming
from dhis2w_fhir_engine.facade.capture.validate import validate_response
from dhis2w_fhir_engine.facade.config import FhirProject, OverwritePosture, ServeAuth
from dhis2w_fhir_engine.facade.response_spool import ResponseLifecycle, ResponseSpool, mint_receipt
from dhis2w_fhir_engine.facade.settings import ServeSettings
from dhis2w_fhir_engine.facade.store import load_compiled_store
from dhis2w_fhir_serve.app import create_app
from dhis2w_fhir_serve.settings import resolve_serve_settings
from fastapi import FastAPI
from fixture_project import AGGREGATE_RESPONSE_FILE, golden

_INSTANCE = "http://dhis2.invalid"
_FHIR_JSON = "application/fhir+json"
_CALLER = "Basic Y2xlcms6aXRzLW93bg=="
_DATA_VALUE_SETS = f"{_INSTANCE}/api/dataValueSets"
_COMPLETENESS = f"{_INSTANCE}/api/completeDataSetRegistrations"

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
        "conflicts": [{"errorCode": "E7641", "object": "Period", "value": "The period is locked"}],
    },
}


def _instance(router: respx.MockRouter) -> None:
    """The reads every connection makes on opening, and a completeness registration that is simply taken."""
    router.get(f"{_INSTANCE}/api/system/info").mock(return_value=httpx.Response(200, json={"version": "2.43.2"}))
    router.post(_COMPLETENESS).mock(return_value=httpx.Response(200, json=_ACCEPTED))


def _connection() -> Dhis2Client:
    """This facade's own connection, not yet opened."""
    return Dhis2Client(base_url=_INSTANCE, auth=BasicAuth(username="service", password="its-own"))


def _refusing_overwrites(project: FhirProject) -> FhirProject:
    """The same project, with `[forward] overwrites = "refuse"` stated."""
    forward = project.config.forward.model_copy(update={"overwrites": OverwritePosture.REFUSE})
    return project.model_copy(update={"config": project.config.model_copy(update={"forward": forward})})


def _service(project: FhirProject, monkeypatch: pytest.MonkeyPatch, **surfaces: Any) -> FastAPI:
    """One service whose facade holds a real connection, mounted with one posture."""

    async def no_value_types(client: object, bound: object, *, progress: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(assembly_module, "fetch_value_types", no_value_types)
    monkeypatch.setattr(forward_module, "fetch_value_types", no_value_types)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        async with (
            _connection() as client,
            open_facade(project, client=client) as facade,
        ):
            attach_facade(application, facade)
            yield

    application = FastAPI(lifespan=lifespan)
    if "read_as_caller" not in surfaces:
        surfaces.setdefault("allow_anonymous", True)
    mount_facade(application, facade_api="/facade", **surfaces)
    return application


@asynccontextmanager
async def _serving(application: FastAPI) -> AsyncGenerator[httpx2.AsyncClient]:
    """Run the application's startup and talk to it with no socket."""
    async with application.router.lifespan_context(application):
        transport = httpx2.ASGITransport(app=application)
        async with httpx2.AsyncClient(transport=transport, base_url="http://service") as client:
            yield client


async def _capture(client: httpx2.AsyncClient, *, authorization: str | None = None) -> httpx2.Response:
    """Submit the one aggregate capture the fixture guide accepts."""
    headers = {"content-type": _FHIR_JSON}
    if authorization is not None:
        headers["Authorization"] = authorization
    return await client.post(
        "/QuestionnaireResponse", content=json.dumps(golden(AGGREGATE_RESPONSE_FILE)), headers=headers
    )


def _receipts_on_disk(project: FhirProject) -> list[str]:
    """Every receipt the spool holds, in any state, as a path under the spool."""
    root = project.project_root / ".serve" / "responses"
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.json")
        if path.is_file() and not path.name.endswith(".report.json")
    )


def _stated(answer: httpx2.Response) -> str:
    """What the first issue of an answer says."""
    return str(answer.json()["issue"][0]["diagnostics"])


async def test_a_capture_dhis2_takes_answers_201_and_is_filed_as_forwarded(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 201 means DHIS2 has the data, which is the whole of what a client needs to know."""
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        posted = router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        async with _serving(_service(capture_project, monkeypatch)) as client:
            answer = await _capture(client)
    assert answer.status_code == 201
    assert "dryRun" not in posted.calls.last.request.url.params
    assert posted.calls.last.request.headers["authorization"].startswith("Basic ")
    assert _stated(answer).startswith("sent to DHIS2 and stored as response ")
    assert answer.headers["location"].startswith("http://service/QuestionnaireResponse/")
    on_disk = _receipts_on_disk(capture_project)
    assert len(on_disk) == 1 and on_disk[0].startswith("forwarded/")


async def test_a_capture_dhis2_refuses_is_answered_with_why_and_stores_nothing(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The person holding the form is the one who can fix it, so nothing lands in a directory nobody reads."""
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(409, json=_REFUSED))
        async with _serving(_service(capture_project, monkeypatch)) as client:
            answer = await _capture(client)
            status = (await client.get("/facade/delivery")).json()
    assert answer.status_code == 409
    assert answer.json()["resourceType"] == "OperationOutcome"
    assert "E7641" in _stated(answer)
    assert _receipts_on_disk(capture_project) == []
    assert status["refused"] == 0


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx2.ConnectError("no route"), id="unreachable"),
        pytest.param(httpx.Response(500, json={"status": "ERROR"}), id="failing"),
        pytest.param(httpx.Response(401, json={"message": "Unauthorized"}), id="no-verdict"),
    ],
)
async def test_dhis2_being_away_holds_the_capture_and_says_it_has_not_been_sent(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch, failure: Any
) -> None:
    """DHIS2 never ruled on it, so it is kept, and the answer is not a 201 that would read as done."""
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        router.post(_DATA_VALUE_SETS).mock(side_effect=[failure])
        async with _serving(_service(capture_project, monkeypatch)) as client:
            answer = await _capture(client)
            status = (await client.get("/facade/delivery")).json()
    assert answer.status_code == 202
    assert _stated(answer).startswith("not yet sent to DHIS2: stored as response ")
    assert _stated(answer).endswith("; this server keeps trying")
    assert answer.headers["location"].startswith("http://service/QuestionnaireResponse/")
    assert status["waiting"] == 1
    assert status["last_problem"]
    on_disk = _receipts_on_disk(capture_project)
    assert len(on_disk) == 1 and on_disk[0].startswith("received/")


async def _held_deliverer(
    project: FhirProject, monkeypatch: pytest.MonkeyPatch, client: Dhis2Client, **fields: Any
) -> CaptureDeliverer:
    """A deliverer holding one capture DHIS2 was away for, opened with nothing retrying it yet."""

    async def no_value_types(client: object, bound: object, *, progress: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(assembly_module, "fetch_value_types", no_value_types)
    monkeypatch.setattr(forward_module, "fetch_value_types", no_value_types)
    async with open_facade(project, client=client) as facade:
        deliverer = facade.deliverer(**fields)
        validated = facade.validate_capture(json.dumps(golden(AGGREGATE_RESPONSE_FILE)).encode())
        held = await deliverer.deliver(validated)
        assert held.kind is DeliveryKind.HELD
        return deliverer


async def test_the_server_sends_a_held_capture_on_its_own_when_dhis2_comes_back(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody runs anything: the capture lands, and the status stops saying there is a problem."""
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        route = router.post(_DATA_VALUE_SETS).mock(side_effect=httpx2.ConnectError("no route"))
        async with _connection() as client:
            deliverer = await _held_deliverer(capture_project, monkeypatch, client)
            assert deliverer.status().waiting == 1
            await deliverer.attempt()
            assert deliverer.status().waiting == 1
            assert deliverer.status().last_problem

            route.mock(side_effect=None, return_value=httpx.Response(200, json=_ACCEPTED))
            await deliverer.attempt()
    status = deliverer.status()
    assert status.waiting == 0
    assert status.refused == 0
    assert status.last_problem is None
    assert status.last_attempt_at is not None
    assert all(path.startswith("forwarded/") for path in _receipts_on_disk(capture_project))


async def test_a_later_capture_queues_behind_the_ones_waiting(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A visit posted ahead of the registration it belongs to is one DHIS2 refuses, so nothing overtakes the queue."""
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        route = router.post(_DATA_VALUE_SETS).mock(side_effect=httpx2.ConnectError("no route"))
        async with _serving(_service(capture_project, monkeypatch)) as client:
            first = await _capture(client)
            attempts = route.call_count
            route.mock(side_effect=None, return_value=httpx.Response(200, json=_ACCEPTED))
            second = await _capture(client)
    assert first.status_code == 202
    assert second.status_code == 202
    assert attempts >= 1
    assert _stated(second).endswith("; this server keeps trying")


async def test_a_capture_refused_after_its_submitter_has_gone_is_counted_and_logged(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Nobody is there to answer, so it has to be somewhere someone will see it."""
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        route = router.post(_DATA_VALUE_SETS).mock(side_effect=httpx2.ConnectError("no route"))
        async with _connection() as client:
            deliverer = await _held_deliverer(capture_project, monkeypatch, client)
            route.mock(side_effect=None, return_value=httpx.Response(409, json=_REFUSED))
            with caplog.at_level(logging.WARNING):
                await deliverer.attempt()
    status = deliverer.status()
    assert status.waiting == 0
    assert status.refused == 1
    assert "after its submitter had gone" in caplog.text
    assert all(path.startswith("rejected/") for path in _receipts_on_disk(capture_project))


async def test_an_attempt_at_an_empty_queue_asks_dhis2_nothing(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quiet server has no reason to keep knocking on a large instance."""
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        posted = router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        async with _serving(_service(capture_project, monkeypatch)) as client:
            status = (await client.get("/facade/delivery")).json()
    assert not posted.called
    assert status["waiting"] == 0
    assert status["last_attempt_at"] is None


async def test_a_worker_is_not_stopped_by_an_error_nobody_expected(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker that died quietly would leave the queue stuck with nothing saying so."""
    attempts = 0

    @asynccontextmanager
    async def breaks_once() -> AsyncGenerator[Dhis2Client]:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("something nobody expected")
        yield  # pragma: no cover

    deliverer = CaptureDeliverer(
        project=capture_project,
        spool=_spool_holding_one(capture_project),
        connect=breaks_once,
        retry_seconds=0.01,
    )
    deliverer.start()
    while attempts < 2:
        await _a_moment()
    await deliverer.stop()
    assert attempts >= 2
    assert deliverer.status().last_problem == "RuntimeError: something nobody expected"
    assert deliverer.status().waiting == 1


def _spool_holding_one(project: FhirProject, *, held_for_retry: bool = True) -> ResponseSpool:
    """The project's spool with one capture in its queue, which a server either took on sending or merely found."""
    spool = ResponseSpool.at(project.project_root)
    validated = validate_response(
        json.dumps(golden(AGGREGATE_RESPONSE_FILE)).encode(),
        CaptureIndexCache(),
        CaptureNaming.from_project(project),
        load_compiled_store(project),
        False,
    )
    spool.save(mint_receipt(validated).model_copy(update={"held_for_retry": held_for_retry}))
    return spool


async def _a_moment() -> None:
    """Let the worker run."""
    await asyncio.sleep(0.01)


async def test_starting_a_server_does_not_send_what_it_found_in_the_queue(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old test submissions can be sitting there, and a restart or an upgrade must not write them to a real DHIS2."""
    _spool_holding_one(capture_project, held_for_retry=False)
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        posted = router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        async with _serving(_service(capture_project, monkeypatch)) as client:
            await asyncio.sleep(0.05)
            status = (await client.get("/facade/delivery")).json()
    assert not posted.called
    assert status["waiting"] == 1
    assert status["retrying"] == 0
    on_disk = _receipts_on_disk(capture_project)
    assert len(on_disk) == 1 and on_disk[0].startswith("received/")


async def test_what_a_server_found_does_not_hold_up_a_new_capture(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new capture goes at once, and the old one stays exactly where it was."""
    _spool_holding_one(capture_project, held_for_retry=False)
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        posted = router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        async with _serving(_service(capture_project, monkeypatch)) as client:
            answer = await _capture(client)
    assert answer.status_code == 201
    assert posted.call_count == 1
    assert sorted(path.split("/")[0] for path in _receipts_on_disk(capture_project)) == ["forwarded", "received"]


async def test_a_held_capture_says_so_on_its_file_and_a_restart_goes_on_trying_it(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart during an outage must not strand what the last run took on, and must still leave the rest alone."""
    _spool_holding_one(capture_project, held_for_retry=False)
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        route = router.post(_DATA_VALUE_SETS).mock(side_effect=httpx2.ConnectError("no route"))
        async with _serving(_service(capture_project, monkeypatch)) as client:
            held = await _capture(client)
        assert held.status_code == 202
        held_id = held.headers["location"].rsplit("/", 1)[-1]

        route.mock(side_effect=None, return_value=httpx.Response(200, json=_ACCEPTED))
        async with _connection() as connection, open_facade(capture_project, client=connection) as facade:
            deliverer = facade.deliverer()
            assert deliverer.status().retrying == 1
            await deliverer.attempt()
    assert route.call_count >= 1
    sent = facade.spool.get(held_id)
    assert sent is not None
    assert sent.lifecycle is ResponseLifecycle.FORWARDED
    assert deliverer.status().waiting == 1
    assert deliverer.status().retrying == 0


async def test_stopping_leaves_the_queue_and_no_worker_behind(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server shutting down loses nothing, and leaves no task running."""
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        router.post(_DATA_VALUE_SETS).mock(side_effect=httpx2.ConnectError("no route"))
        async with _serving(_service(capture_project, monkeypatch)) as client:
            await _capture(client)
    on_disk = _receipts_on_disk(capture_project)
    assert len(on_disk) == 1 and on_disk[0].startswith("received/")


async def test_hold_keeps_a_capture_and_asks_dhis2_nothing(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is what a deployment states when something else is meant to send the queue."""
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        posted = router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        async with _serving(_service(capture_project, monkeypatch, delivery=CaptureDelivery.HOLD)) as client:
            answer = await _capture(client)
    assert answer.status_code == 202
    assert not posted.called
    assert _stated(answer).endswith("; nothing sends it until `d2w fhir forward` or `drain` runs")


async def test_validate_only_has_dhis2_grade_it_and_writes_nothing(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It needs no caller's credentials to be worth having: it is how a service is tried out."""
    service = _service(capture_project, monkeypatch, delivery=CaptureDelivery.VALIDATE_ONLY)
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        posted = router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        registered = router.post(_COMPLETENESS)
        async with _serving(service) as client:
            answer = await _capture(client)
    assert answer.status_code == 200
    assert posted.calls.last.request.url.params["dryRun"] == "true"
    assert not registered.called
    assert "location" not in answer.headers
    assert _receipts_on_disk(capture_project) == []


async def test_validate_only_with_dhis2_away_fails_rather_than_keeping_anything(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture being tried out is not one to send later."""
    service = _service(capture_project, monkeypatch, delivery=CaptureDelivery.VALIDATE_ONLY)
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        router.post(_DATA_VALUE_SETS).mock(side_effect=httpx2.ConnectError("no route"))
        async with _serving(service) as client:
            answer = await _capture(client)
    assert answer.status_code == 502
    assert "send it again" in _stated(answer)
    assert _receipts_on_disk(capture_project) == []


async def test_a_server_with_no_connection_holds_the_capture_and_says_so(capture_project: FhirProject) -> None:
    """There is nothing to post through, and the answer must not read as though there were."""
    application = create_app(ServeSettings(project_dir=capture_project.project_root))
    async with _serving(application) as client:
        answer = await _capture(client)
        status = (await client.get("/facade/delivery")).json()
    assert answer.status_code == 202
    assert _stated(answer).endswith("; nothing sends it until `d2w fhir forward` or `drain` runs")
    assert status["posts_as"] == "nobody"
    assert status["waiting"] == 1


async def test_a_refused_overwrite_is_answered_at_once_and_sends_nothing(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`[forward] overwrites = "refuse"` holds whichever way a capture reaches DHIS2."""
    project = _refusing_overwrites(capture_project)
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        posted = router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        async with _serving(_service(project, monkeypatch)) as client:
            first = await _capture(client)
            second = await _capture(client)
    assert first.status_code == 201
    assert second.status_code == 409
    assert posted.call_count == 1
    assert "already sent to DHIS2" in _stated(second)
    assert len(_receipts_on_disk(capture_project)) == 1


async def test_a_refused_overwrite_holds_when_posting_as_the_caller_too(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller's own rights decide what DHIS2 lets them write, and the project still decides what it re-sends."""
    project = _refusing_overwrites(capture_project)
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        router.get(f"{_INSTANCE}/api/me").mock(return_value=httpx.Response(200, json={"username": "clerk"}))
        posted = router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        async with _serving(_service(project, monkeypatch, read_as_caller=True)) as client:
            first = await _capture(client, authorization=_CALLER)
            second = await _capture(client, authorization=_CALLER)
    assert first.status_code == 201
    assert second.status_code == 409
    assert posted.call_count == 1


async def test_an_allowed_overwrite_names_what_it_replaced(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DHIS2 replaces a value without saying so, and this is the one place that can say it."""
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        async with _serving(_service(capture_project, monkeypatch)) as client:
            await _capture(client)
            second = await _capture(client)
    assert second.status_code == 201
    warnings = [issue for issue in second.json()["issue"] if issue["severity"] == "warning"]
    assert warnings
    assert any(issue["diagnostics"].startswith("replaced ") for issue in warnings)


async def test_the_library_submits_a_capture_the_same_way(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A service with no routes gets what the routes get."""

    async def no_value_types(client: object, bound: object, *, progress: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(assembly_module, "fetch_value_types", no_value_types)
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        async with _connection() as client, open_facade(capture_project, client=client) as facade:
            outcome = await facade.submit(json.dumps(golden(AGGREGATE_RESPONSE_FILE)).encode(), submitted_by="a-system")
    assert outcome.kind is DeliveryKind.SENT
    stored = facade.spool.get(outcome.envelope.response_id)
    assert stored is not None
    assert stored.lifecycle is ResponseLifecycle.FORWARDED
    assert stored.submitted_by == "a-system"


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        pytest.param(
            {"live": True, "capture_delivery": CaptureDelivery.AT_ONCE},
            CAPTURE_AT_ONCE_REST_DOCUMENTATION,
            id="at-once",
        ),
        pytest.param(
            {"live": True, "auth": ServeAuth.DHIS2, "capture_delivery": CaptureDelivery.AT_ONCE},
            CAPTURE_AS_CALLER_REST_DOCUMENTATION,
            id="as-caller",
        ),
        pytest.param(
            {"live": True, "capture_delivery": CaptureDelivery.HOLD}, CAPTURE_HELD_REST_DOCUMENTATION, id="hold"
        ),
        pytest.param({}, CAPTURE_HELD_REST_DOCUMENTATION, id="no-connection"),
        pytest.param(
            {"live": True, "capture_delivery": CaptureDelivery.VALIDATE_ONLY},
            CAPTURE_VALIDATED_REST_DOCUMENTATION,
            id="validate-only",
        ),
    ],
)
def test_metadata_says_what_a_capture_will_do_on_this_server(
    capture_project: FhirProject, settings: dict[str, Any], expected: str
) -> None:
    """A client reads this before it submits anything, so it must not promise a 201 this server will not give."""
    assert _rest_documentation(ServeSettings(project_dir=capture_project.project_root, **settings)) == expected


def _found_profile(named: bool) -> GenerationProfile:
    """A profile resolved for a run, somebody having named it or this machine having fallen back to it."""
    profile = Profile(base_url=_INSTANCE, auth="basic", username="someone", password="its-own")
    return GenerationProfile(name="found", origin="test", profile=profile, named=named)


def _with_serve(project: FhirProject, **keys: Any) -> FhirProject:
    """The same project, with these `[serve]` keys stated."""
    serve = project.config.serve.model_copy(update=keys)
    return project.model_copy(update={"config": project.config.model_copy(update={"serve": serve})})


@pytest.mark.parametrize(
    ("named", "stated", "expected", "held_for_it"),
    [
        pytest.param(True, None, CaptureDelivery.AT_ONCE, False, id="named-posts-at-once"),
        pytest.param(False, None, CaptureDelivery.HOLD, True, id="found-holds-so-a-test-submit-writes-nothing"),
        pytest.param(False, CaptureDelivery.AT_ONCE, CaptureDelivery.AT_ONCE, False, id="found-and-asked-for-posts"),
        pytest.param(True, CaptureDelivery.HOLD, CaptureDelivery.HOLD, False, id="named-and-asked-to-hold-holds"),
    ],
)
def test_a_run_only_posts_at_once_to_a_dhis2_somebody_named(
    capture_project: FhirProject,
    monkeypatch: pytest.MonkeyPatch,
    named: bool,
    stated: CaptureDelivery | None,
    expected: CaptureDelivery,
    held_for_it: bool,
) -> None:
    """Profiles are discovered, and the one found could be an administrator's login to anything."""
    monkeypatch.setattr(
        "dhis2w_fhir_serve.settings._resolved_generation", lambda *_args, **_keys: _found_profile(named)
    )
    invocation = resolve_serve_settings(capture_project, delivery=stated)
    assert invocation.settings.capture_delivery is expected
    assert invocation.held_for_an_unnamed_profile is held_for_it


def test_delivery_comes_off_the_project_and_a_flag_beats_it(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment states it once, and one run may state otherwise."""
    monkeypatch.setattr("dhis2w_fhir_serve.settings._resolved_generation", lambda *_args, **_keys: _found_profile(True))
    holding = _with_serve(capture_project, delivery=CaptureDelivery.HOLD)
    assert resolve_serve_settings(holding).settings.capture_delivery is CaptureDelivery.HOLD
    stated = resolve_serve_settings(holding, delivery=CaptureDelivery.VALIDATE_ONLY)
    assert stated.settings.capture_delivery is CaptureDelivery.VALIDATE_ONLY


def test_a_settings_object_posts_nothing_until_told(capture_project: FhirProject) -> None:
    """Whoever builds one by hand has not said which DHIS2 they mean."""
    assert ServeSettings(project_dir=capture_project.project_root).capture_delivery is CaptureDelivery.HOLD


def test_a_caller_presenting_their_own_account_needs_no_named_profile(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Their credentials say which DHIS2 they mean, and DHIS2 applies their rights rather than an administrator's."""
    monkeypatch.setattr(
        "dhis2w_fhir_serve.settings._resolved_generation", lambda *_args, **_keys: _found_profile(False)
    )
    monkeypatch.setattr("dhis2w_fhir_serve.settings.preflight_auth", lambda **_keys: None)
    invocation = resolve_serve_settings(capture_project, auth=ServeAuth.DHIS2, live=True)
    assert invocation.settings.capture_delivery is CaptureDelivery.AT_ONCE
    assert not invocation.held_for_an_unnamed_profile


def test_a_profile_says_whether_somebody_named_it(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falling back to a machine's default is the one case that names nothing."""
    found = ResolvedProfile(
        name="found",
        profile=Profile(base_url=_INSTANCE, auth="basic", username="someone", password="its-own"),
        source="global-toml",
        layer="global-toml",
    )
    monkeypatch.setattr("dhis2w_fhir.service.resolve", lambda _name: found)
    monkeypatch.delenv("DHIS2_PROFILE", raising=False)
    assert not resolve_generation_profile(capture_project).named
    assert resolve_generation_profile(capture_project, "found").named
    monkeypatch.setenv("DHIS2_PROFILE", "found")
    assert resolve_generation_profile(capture_project).named


async def test_a_compiled_run_with_a_profile_posts_through_a_connection_it_opens_per_attempt(
    capture_project: FhirProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guide off disk is no reason to leave captures in a folder, and DHIS2 being away must not stop it starting."""
    opened = 0

    @asynccontextmanager
    async def fake_open_client(project: FhirProject, settings: ServeSettings) -> AsyncGenerator[Dhis2Client]:
        nonlocal opened
        opened += 1
        async with Dhis2Client(base_url=_INSTANCE, auth=BasicAuth(username="profile", password="its-own")) as client:
            yield client

    async def no_value_types(client: object, bound: object, *, progress: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr("dhis2w_fhir_serve.runtime.open_delivery_client", fake_open_client)
    monkeypatch.setattr("dhis2w_fhir_engine.facade.delivery.fetch_value_types", no_value_types)
    application = create_app(
        ServeSettings(
            project_dir=capture_project.project_root,
            dhis2_base_url=_INSTANCE,
            capture_delivery=CaptureDelivery.AT_ONCE,
        )
    )
    with respx.mock(assert_all_called=False) as router:
        _instance(router)
        router.post(_DATA_VALUE_SETS).mock(return_value=httpx.Response(200, json=_ACCEPTED))
        async with _serving(application) as client:
            assert opened == 0
            answer = await _capture(client)
    assert answer.status_code == 201
    assert opened == 1
