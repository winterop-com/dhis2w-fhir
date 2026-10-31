"""`POST /QuestionnaireResponse`: the one write the facade accepts, and the refusal in its place.

A project that sets `[serve] capture = false` mounts `refusal_router` instead of `router`, and the
address answers 405 with an OperationOutcome naming the key. The refusal is a route rather than the
405 Starlette would produce on its own from the read router's GET: both are the same status and the
same shape, and only one of them says why. Nothing else about the resource type moves - the receipts
already spooled are read, searched, and counted at the same paths.

The interaction is FHIR's `create`, answered by what became of the capture: 201 once DHIS2 has taken it, 202 while this
server holds it unsent, and 200 where DHIS2 only graded it under validate-only. A 201 or a 202 names where the receipt
is served from in `Location`. A refusal answers with DHIS2's reasons and stores nothing, because the submitter is the
one who can act on it. `dhis2w_fhir_engine.facade.delivery` is what decides, and says why.

The body is read as raw bytes rather than through a FastAPI request model. A capture is validated
against the served IG - its questionnaires, its terminology, its profiles - which is a contract a
FastAPI parameter model cannot express, and reading the bytes here is also what keeps the stored
copy byte-faithful to what the client sent.

A held capture is durable: the receipt is fsynced and its directory entry with it before the 202 goes out, so a 202 is
a promise that survives power loss.

A CORRECTION OR A WITHDRAWAL IS REFUSED HERE where the project's dials are off. `[forward]
corrections` and `[forward] withdrawals` are read off `fhir.toml` onto the capture state, and a
submission carrying `status = "amended"` or `status = "entered-in-error"` against an off dial is
answered 422 with the key named. With the dial on it goes the way any other capture does, status and
all - see `dhis2w_fhir_engine.facade.capture.validate`.

WHO SUBMITTED IT is stamped on the receipt where this run established anybody. Under
`[serve] auth = "dhis2"` the check has already read `/api/me` as the caller, so the username DHIS2
answered with is on the request and goes onto the envelope. Under every other posture there is no
person to name and the field stays absent - a static token is not a submitter, and a server that
authenticates nobody knows nobody.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from dhis2w_fhir_engine.facade.api.auth import request_identity
from dhis2w_fhir_engine.facade.api.passthrough import caller_reader
from dhis2w_fhir_engine.facade.api.routes.context import delivery_state, serve_context
from dhis2w_fhir_engine.facade.capability import QUESTIONNAIRE_RESPONSE_RESOURCE_TYPE
from dhis2w_fhir_engine.facade.capture.index import CaptureIndexCache
from dhis2w_fhir_engine.facade.capture.naming import CaptureNaming
from dhis2w_fhir_engine.facade.capture.outcome import (
    CaptureRejection,
    held_outcome,
    rejection_outcome,
    sent_outcome,
    validated_outcome,
)
from dhis2w_fhir_engine.facade.capture.validate import (
    CaptureLifecyclePostures,
    ValidatedCapture,
    validate_response,
)
from dhis2w_fhir_engine.facade.config import CaptureDelivery
from dhis2w_fhir_engine.facade.delivery import DeliveryKind, DeliveryOutcome
from dhis2w_fhir_engine.facade.refusals import (
    FHIR_JSON_MEDIA_TYPE,
    CaptureDisabledError,
    PackagePublishesNoFormsError,
    UnsupportedMediaTypeError,
)
from dhis2w_fhir_engine.facade.status import ORGANISATION_UNIT_PACKAGE

#: Where the capture state is held on the app. It is deliberately not part of `ServeContext`: the
#: context is everything the lifespan loaded, and this is built by the first request that needs a
#: form read - a capture, or a record read projecting one entity's events through the same forms -
#: and filled in as questionnaires are met.
CAPTURE_STATE_ATTRIBUTE = "capture"

#: The JSON media types a capture body may be declared as. `application/fhir+json` is what FHIR
#: asks for, `application/json` is what every generic HTTP client sends by default, and any other
#: `+json` structured syntax still says the bytes are JSON. A body declared as anything else -
#: XML, a form post, plain text - is refused with 415 before the bytes are read, because the
#: declaration says the client is not sending what this endpoint parses. An absent Content-Type
#: declares nothing and the body is read as the JSON it has to be either way.
_JSON_MEDIA_TYPES = frozenset({FHIR_JSON_MEDIA_TYPE, "application/json"})
_JSON_MEDIA_TYPE_SUFFIX = "+json"

router = APIRouter()

#: What a viewer-posture run mounts in the create route's place - the same path, answering why.
refusal_router = APIRouter()

#: What a package mounts there instead - the same path, answering with what the project is.
package_refusal_router = APIRouter()


@refusal_router.post(f"/{QUESTIONNAIRE_RESPONSE_RESOURCE_TYPE}")
async def refuse_questionnaire_response(request: Request) -> Response:
    """Refuse a submission this project does not receive, naming the key that decided it."""
    raise CaptureDisabledError(QUESTIONNAIRE_RESPONSE_RESOURCE_TYPE)


@package_refusal_router.post(f"/{QUESTIONNAIRE_RESPONSE_RESOURCE_TYPE}")
async def refuse_questionnaire_response_on_a_package(request: Request) -> Response:
    """Refuse a submission to a package, which publishes no form for one to answer.

    The sentence says what the project is rather than what this document lacks: a package's forms
    are not missing, they are somewhere else, and the form-type check further down would have told
    a client to fix a body that no body could fix.
    """
    # Mounted only where `[ig] publishes` states something, so the content value is always there;
    # the one content value a package may hold names itself where a reader would otherwise see None.
    publishes = serve_context(request).settings.publishes or ORGANISATION_UNIT_PACKAGE
    raise PackagePublishesNoFormsError(QUESTIONNAIRE_RESPONSE_RESOURCE_TYPE, publishes)


class CaptureState(BaseModel):
    """What a capture request needs beyond the serve context: the project's names, dials, and index cache."""

    model_config = ConfigDict(frozen=True)

    naming: CaptureNaming
    indexes: CaptureIndexCache = Field(default_factory=CaptureIndexCache)
    postures: CaptureLifecyclePostures = CaptureLifecyclePostures()
    """Whether this project receives a submission that corrects, or one that retracts, a forwarded receipt.

    A project-level fact read off `fhir.toml` exactly as the naming above is, and held here for the
    same reason: it is settled once by the project and never by a request. `ServeSettings` carries
    what the *run* was invoked with; these two are what the project says, and `d2w fhir forward`
    reads the same keys out of the same file.
    """


@router.post(f"/{QUESTIONNAIRE_RESPONSE_RESOURCE_TYPE}")
async def create_questionnaire_response(request: Request) -> Response:
    """Receive one QuestionnaireResponse: validate it against the served IG, store it, and say where it lives."""
    _require_json_body(request)
    context = serve_context(request)
    state = capture_state(request)
    try:
        validated = validate_response(
            await request.body(),
            state.indexes,
            state.naming,
            context.store,
            context.settings.strict_codes,
            state.postures,
        )
    except CaptureRejection as rejection:
        return JSONResponse(
            status_code=rejection.http_status,
            content=rejection_outcome(rejection.issues).model_dump(mode="json", exclude_none=True, by_alias=True),
            media_type=FHIR_JSON_MEDIA_TYPE,
        )
    delivery = delivery_state(request)
    posts_as_the_caller = delivery.posts_as_caller and delivery.mode is not CaptureDelivery.HOLD
    caller = await caller_reader(request) if posts_as_the_caller else None
    identity = request_identity(request)
    outcome = await delivery.deliver(
        validated, submitted_by=None if identity is None else identity.username, caller=caller
    )
    return _answered(request, outcome, validated)


def _require_json_body(request: Request) -> None:
    """Refuse a body declared in a media type this endpoint does not parse, with 415 and an OperationOutcome."""
    declared = request.headers.get("content-type")
    if declared is None or not declared.strip():
        return
    media_type = declared.split(";", 1)[0].strip().lower()
    if media_type in _JSON_MEDIA_TYPES or media_type.endswith(_JSON_MEDIA_TYPE_SUFFIX):
        return
    raise UnsupportedMediaTypeError(media_type)


def capture_state(request: Request) -> CaptureState:
    """The capture state of this app, built from the served project the first time a form is read.

    Shared with the record surface, which projects one entity's events through the same naming and
    the same index cache a submission is validated against - one form typed one way, whichever
    direction a value is travelling. `dhis2w_fhir_engine.facade.api.routes.history` is the other caller.
    """
    held: CaptureState | None = getattr(request.app.state, CAPTURE_STATE_ATTRIBUTE, None)
    if held is not None:
        return held
    project = serve_context(request).project
    state = CaptureState(
        naming=CaptureNaming.from_project(project),
        postures=CaptureLifecyclePostures.from_project(project),
    )
    setattr(request.app.state, CAPTURE_STATE_ATTRIBUTE, state)
    return state


def _answered(request: Request, outcome: DeliveryOutcome, validated: ValidatedCapture) -> Response:
    """Answer an accepted capture with what became of it, and where its receipt is served from if there is one."""
    response_id = outcome.envelope.response_id
    replaced = tuple(value.line for value in outcome.overwritten)
    if outcome.kind is DeliveryKind.VALIDATED:
        return _outcome_response(200, validated_outcome(validated.warnings, replaced=replaced), None)
    stated = serve_context(request).settings.service_base
    base_url = (stated if stated is not None else str(request.base_url)).rstrip("/")
    location = f"{base_url}/{QUESTIONNAIRE_RESPONSE_RESOURCE_TYPE}/{response_id}"
    if outcome.kind is DeliveryKind.SENT:
        body = sent_outcome(response_id, validated.subject, validated.warnings, replaced=replaced)
        return _outcome_response(201, body, location)
    body = held_outcome(response_id, validated.subject, validated.warnings, note=outcome.note or "")
    return _outcome_response(202, body, location)


def _outcome_response(status_code: int, body: BaseModel, location: str | None) -> Response:
    """One OperationOutcome as a FHIR response, under the `Location` a stored receipt is served from."""
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True, by_alias=True),
        media_type=FHIR_JSON_MEDIA_TYPE,
        headers=None if location is None else {"Location": location},
    )
