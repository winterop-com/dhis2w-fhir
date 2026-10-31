"""Tests that a capture's identifier names its event, and that "already exists" counts only for the same submission."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dhis2w_client.errors import Dhis2ApiError
from dhis2w_client.generated.v43.tracker import TrackerDataValue, TrackerEvent
from dhis2w_fhir_engine.facade.conversion import capture_identifier_label, receipt_event_uid, response_event_uid
from dhis2w_fhir_engine.facade.forward import event_already_holds
from dhis2w_fhir_engine.r4.resources import QuestionnaireResponse

_FORM = "http://example.org/fhir/Questionnaire/anc-visit"


def _response(response_id: str, *, value: str | None = None, form: str = _FORM) -> QuestionnaireResponse:
    """One response, named by an identifier where `value` is given."""
    body: dict[str, Any] = {"resourceType": "QuestionnaireResponse", "id": response_id, "status": "completed"}
    body["questionnaire"] = form
    if value is not None:
        body["identifier"] = {"system": "urn:example:clinic-app", "value": value}
    return QuestionnaireResponse.model_validate(body)


def test_one_identifier_names_one_event_whatever_receipt_carries_it() -> None:
    """Two receipts of one submission name the same event, so DHIS2 keeps one."""
    first = response_event_uid(_response("a" * 32, value="visit-0001"))
    second = response_event_uid(_response("b" * 32, value="visit-0001"))
    assert first == second
    assert first is not None and len(first) == 11


def test_another_identifier_or_another_form_names_another_event() -> None:
    """A new submission, or one form's numbering meeting another's, is a new event."""
    base = response_event_uid(_response("a" * 32, value="visit-0001"))
    assert response_event_uid(_response("a" * 32, value="visit-0002")) != base
    assert response_event_uid(_response("a" * 32, value="visit-0001", form=_FORM + "-other")) != base


def test_a_form_version_does_not_change_the_event() -> None:
    """The same submission sent against a newer version of its form is still the same visit."""
    plain = response_event_uid(_response("a" * 32, value="visit-0001"))
    assert response_event_uid(_response("a" * 32, value="visit-0001", form=_FORM + "|0.2.0")) == plain


def test_without_an_identifier_the_receipt_names_the_event() -> None:
    """A response the client did not name is named by the receipt this server minted for it."""
    response = _response("c" * 32)
    assert capture_identifier_label(response) is None
    assert response_event_uid(response) == receipt_event_uid("c" * 32)


class _Reader:
    """A connection whose one read answers with a stored event, or fails."""

    def __init__(self, held: dict[str, Any] | None) -> None:
        self.held = held

    async def post_raw(self, path: str, body: Any = None, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Never called by the comparison."""
        raise AssertionError(path)

    async def get_raw(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Answer the read with the stored event, or as DHIS2 answers an event it does not hold."""
        if self.held is None:
            raise Dhis2ApiError(404, "not found", {})
        return self.held


def _event(value: str) -> TrackerEvent:
    """The event a capture posts."""
    return TrackerEvent(
        event="AbCdEfGhIjK",
        programStage="PsAncVisit1",
        orgUnit="DiszpKrYNg8",
        occurredAt=datetime(2026, 10, 31, 4, 0),
        dataValues=[TrackerDataValue(dataElement="DeWeight001", value=value)],
    )


def _held(value: str) -> dict[str, Any]:
    """The same event as DHIS2's read answers it."""
    return {
        "event": "AbCdEfGhIjK",
        "programStage": "PsAncVisit1",
        "orgUnit": "DiszpKrYNg8",
        "occurredAt": "2026-10-31T00:00:00.000",
        "dataValues": [{"dataElement": "DeWeight001", "value": value}],
    }


async def test_the_same_answers_as_dhis2_spells_them_are_the_same_submission() -> None:
    """DHIS2 may write a number or a date in its own form, and that is not a different capture."""
    assert await event_already_holds(_Reader(_held("62.0")), _event("62"))


async def test_other_answers_are_another_submission() -> None:
    """A reused identifier with different answers must not read as delivered."""
    assert not await event_already_holds(_Reader(_held("70")), _event("62"))


async def test_an_event_that_cannot_be_read_back_is_not_counted() -> None:
    """Nothing compared is nothing confirmed."""
    assert not await event_already_holds(_Reader(None), _event("62"))


async def test_a_stage_dhis2_fills_in_for_an_event_program_is_not_a_difference() -> None:
    """An event program's payload names no stage, and DHIS2 stores its one stage."""
    sent = _event("62").model_copy(update={"programStage": None})
    assert await event_already_holds(_Reader(_held("62")), sent)
