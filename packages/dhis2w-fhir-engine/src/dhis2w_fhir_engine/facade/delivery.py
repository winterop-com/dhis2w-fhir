"""What becomes of a capture once it is accepted: posted while the submitter waits, or held and retried by the server.

THE SUBMITTER HEARS DHIS2'S ANSWER, BECAUSE THEY ARE THE ONE WHO CAN ACT ON IT. A refusal handed back in the response
reaches the person holding the form. A refusal filed in a directory later reaches nobody, so a capture goes to DHIS2
at once, a refusal is answered and stores nothing, and a 201 means DHIS2 has the data.

WHO POSTS IT decides what happens when DHIS2 cannot be reached:

- **The caller's own credentials.** This service keeps no caller's password, so it cannot try again later. The capture
  fails, nothing is stored, and the client sends it again.
- **This facade's one connection.** The capture is held in the spool and the answer says it has not been sent. A worker
  retries it while the server runs, so nobody has to run anything. Later captures join it rather than overtake it,
  because a visit posted ahead of the registration it belongs to is one DHIS2 refuses.

THE WORKER SENDS ONLY WHAT THIS SERVER TOOK ON. A receipt it holds for retry says so on its file. Anything else in
`received/` stays there until somebody runs `d2w fhir forward`, because it may be old test submissions, and starting a
server, upgrading one, or turning off `hold` must not write those to whichever DHIS2 it now points at.

AN ANSWER CAN BE LOST AFTER DHIS2 HAS SAVED. A timeout says nothing about whether the write landed, so it is never
reported as nothing having been stored. One capture carries one identity on every attempt, so a retry DHIS2 answers with
"already exists" is that earlier attempt landing rather than a refusal. Under the caller's credentials nothing is kept
to retry, and a client resending mints a new receipt and so a new event, which the answer warns about by naming the
event to read first.

A retried capture can still be refused, and by then its submitter has gone. That refusal is filed under `rejected/`,
logged, and counted by `status`, which is what the capture UI and `GET /facade/delivery` read.

`hold` turns all of this off: captures stay in the spool until `d2w fhir forward` posts them. A server with no DHIS2
connection at all holds captures the same way, because it has no other choice.

`d2w fhir serve` HOLDS UNTIL ASKED, under its one account. `profile` says which DHIS2 to read metadata from, and a
project can point it at a national instance to build a guide, so `[serve] delivery = "at-once"` is what permits writing.
A caller presenting their own account has asked, and an application handing `open_facade` a connection has too.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from enum import StrEnum

import httpx2
from dhis2w_client import Dhis2Client
from dhis2w_client.errors import AuthenticationError, Dhis2ApiError, Dhis2ClientError
from pydantic import BaseModel, ConfigDict, PrivateAttr

from dhis2w_fhir_engine.facade.capture.validate import ValidatedCapture
from dhis2w_fhir_engine.facade.config import (
    DEFAULT_DELIVERY_RETRY_SECONDS,
    CaptureDelivery,
    FhirProject,
    OverwritePosture,
)
from dhis2w_fhir_engine.facade.conversion.artifacts import (
    CompiledArtifacts,
    CompiledIgMissingError,
    bound_question_uids,
    build_project_context,
    load_compiled_artifacts,
)
from dhis2w_fhir_engine.facade.conversion.schemas import ConversionContext, ConversionNaming, ConversionResult
from dhis2w_fhir_engine.facade.conversion.translator import translate_response
from dhis2w_fhir_engine.facade.forward import (
    ImportPoster,
    claim_completeness,
    configured_coded_answer_mode,
    drain_responses,
    fetch_value_types,
    post_translation,
    spool_layout,
)
from dhis2w_fhir_engine.facade.forward_records import ForwardImportRecord, ForwardImportVerdict
from dhis2w_fhir_engine.facade.live_inputs import fetch_live_artifacts
from dhis2w_fhir_engine.facade.overwrite import OverwrittenValue, aggregate_cells, build_forwarded_cell_index
from dhis2w_fhir_engine.facade.progress import StepAnnouncer
from dhis2w_fhir_engine.facade.refusals import (
    CaptureNotTranslatedError,
    CaptureOutcomeUnknownError,
    CaptureOverwriteRefusedError,
    CaptureRefusedByInstanceError,
    UpstreamError,
)
from dhis2w_fhir_engine.facade.response_spool import (
    ResponseLifecycle,
    ResponseSpool,
    StoredReceipt,
    StoredResponseEnvelope,
    current_instant,
    mint_receipt,
)
from dhis2w_fhir_engine.facade.spool import SpoolLockedError
from dhis2w_fhir_engine.r4.resources import QuestionnaireResponse

logger = logging.getLogger(__name__)

#: From here up an answer is about the server rather than the request, and a gateway says it after DHIS2 may have saved.
_FIRST_SERVER_ERROR_STATUS = 500

#: What a held capture says where this server is the one trying again.
HELD_AND_RETRYING = "this server keeps trying"

#: What a held capture says where DHIS2 may have saved it and its answer never arrived.
HELD_ANSWER_LOST = (
    "its answer never arrived, so DHIS2 may already have it; this server keeps trying and will not send it twice"
)

#: What a held capture says where nothing here will send it on its own.
HELD_UNTIL_DRAINED = "nothing sends it until `d2w fhir forward` or `drain` runs"

#: What a held capture says on `d2w fhir serve`, where one line in `fhir.toml` changes what happens to the next one.
HELD_UNTIL_ASKED = (
    'nothing sends it until `d2w fhir forward` runs, and `[serve] delivery = "at-once"` posts new captures'
)


class DeliveryKind(StrEnum):
    """What became of one accepted capture."""

    #: DHIS2 took it, and the receipt is filed as forwarded.
    SENT = "sent"

    #: DHIS2 graded it under validate-only and would take it. Nothing was written or stored.
    VALIDATED = "validated"

    #: It has not reached DHIS2. The receipt is in the queue.
    HELD = "held"


class DeliveryActor(StrEnum):
    """Whose rights DHIS2 applies to the captures this facade posts."""

    #: Each caller, under their own credentials.
    CALLER = "caller"

    #: The one account behind this facade's connection, for everyone.
    CONNECTION = "connection"

    #: Nobody: this facade holds no connection to DHIS2.
    NOBODY = "nobody"


class DeliveryOutcome(BaseModel):
    """What one accepted capture became, for whoever answers the submitter."""

    model_config = ConfigDict(frozen=True)

    kind: DeliveryKind
    envelope: StoredResponseEnvelope
    note: str | None = None
    """What happens next to a held capture, in words the submitter can read."""

    overwritten: tuple[OverwrittenValue, ...] = ()
    """Every value this capture replaced that an earlier receipt of this spool had sent."""

    may_have_arrived: bool = False
    """Whether DHIS2 may already have this held capture, because the post went out and its answer never came back."""


class DeliveryStatus(BaseModel):
    """What a running facade says about the captures it has not landed in DHIS2."""

    model_config = ConfigDict(frozen=True)

    delivery: CaptureDelivery
    posts_as: DeliveryActor
    waiting: int = 0
    """Captures in the queue that have not reached DHIS2, and that nothing has refused."""

    retrying: int = 0
    """How many of those this server is sending on its own. The rest stay until `d2w fhir forward` runs."""

    refused: int = 0
    """Captures that were refused after their submitter had gone, which someone has to look at."""

    last_attempt_at: str | None = None
    """When this server last tried the queue, as a FHIR `instant`. None where it has not tried since it started."""

    last_problem: str | None = None
    """Why the last attempt could not reach DHIS2, and None where it did."""


class _NoVerdictError(Dhis2ClientError):
    """Something answered in DHIS2's place, so the answer says nothing about the capture."""


class _AnswerLostError(Dhis2ClientError):
    """A post went out and no answer about it came back, so DHIS2 may or may not have taken it."""

    def __init__(self, problem: str, event_uid: str | None) -> None:
        super().__init__(problem)
        self.event_uid = event_uid


def _never_arrived(error: BaseException) -> bool:
    """Whether a failed post cannot have reached DHIS2: no connection was made, or DHIS2 turned it away first."""
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, httpx2.ConnectError | httpx2.ConnectTimeout | httpx2.PoolTimeout | AuthenticationError):
            return True
        if isinstance(seen, Dhis2ApiError) and seen.status_code < _FIRST_SERVER_ERROR_STATUS:
            return True
        seen = seen.__cause__
    return False


class _DeliveryGuide(BaseModel):
    """What a capture is translated against, read the first time one is posted."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    artifacts: CompiledArtifacts
    conversion: ConversionContext


class CaptureDeliverer(BaseModel):
    """Posts this facade's captures to DHIS2 at once, and keeps trying the ones it could not."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    project: FhirProject
    spool: ResponseSpool
    mode: CaptureDelivery = CaptureDelivery.AT_ONCE
    posts_as_caller: bool = False
    connect: Callable[[], AbstractAsyncContextManager[Dhis2Client]] | None = None
    """Opens this facade's connection for one attempt, and None where it holds none. It may raise: DHIS2 can be away."""

    retry_seconds: float = DEFAULT_DELIVERY_RETRY_SECONDS
    held_note: str = HELD_UNTIL_DRAINED
    """What a capture this facade will not send says happens next."""

    artifacts: CompiledArtifacts | None = None
    conversion: ConversionContext | None = None

    _guide_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
    _guide: _DeliveryGuide | None = PrivateAttr(default=None)
    _worker: asyncio.Task[None] | None = PrivateAttr(default=None)
    _wake: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    _queue_owed: bool = PrivateAttr(default=False)
    _problems_noted: int = PrivateAttr(default=0)
    _last_attempt_at: str | None = PrivateAttr(default=None)
    _last_problem: str | None = PrivateAttr(default=None)

    @property
    def retries(self) -> bool:
        """Whether this facade tries the queue on its own, which takes one connection and no caller to answer to."""
        return self.mode is CaptureDelivery.AT_ONCE and self.connect is not None and not self.posts_as_caller

    async def deliver(
        self,
        validated: ValidatedCapture,
        *,
        submitted_by: str | None = None,
        caller: ImportPoster | None = None,
    ) -> DeliveryOutcome:
        """Send one validated capture on its way, and say what became of it.

        `caller` posts it under the caller's own credentials, and is None where this facade's connection does.
        A refusal raises, because it is the submitter's to hear: `CaptureNotTranslatedError`,
        `CaptureOverwriteRefusedError`, or `CaptureRefusedByInstanceError`. Nothing is stored for any of them.
        """
        envelope = mint_receipt(validated, submitted_by=submitted_by)
        if self.mode is CaptureDelivery.HOLD:
            return await self._held(envelope, self.held_note, retrying=False)
        if self.connect is None:
            if self.mode is CaptureDelivery.VALIDATE_ONLY or caller is not None:
                raise UpstreamError("this facade holds no connection to DHIS2, so nothing could grade this capture")
            return await self._held(envelope, self.held_note, retrying=False)
        if caller is None and self._worker is not None and self._queue_owed:
            self._wake.set()
            return await self._held(envelope, HELD_AND_RETRYING, retrying=True)
        try:
            async with self.connect() as client:
                return await self._post(client if caller is None else caller, client, envelope)
        except _AnswerLostError as error:
            return await self._answer_lost(envelope, error, caller=caller)
        except (Dhis2ClientError, httpx2.HTTPError) as error:
            return await self._could_not_reach(envelope, str(error) or type(error).__name__, caller=caller)

    def start(self) -> None:
        """Start trying the queue, where this facade does. It needs a running event loop."""
        if not self.retries or self._worker is not None:
            return
        self._queue_owed = self._retrying() > 0
        self._worker = asyncio.create_task(self._keep_delivering(), name="capture-delivery")

    async def stop(self) -> None:
        """Stop trying the queue. What is in it stays there for the next start."""
        worker, self._worker = self._worker, None
        if worker is None:
            return
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    async def attempt(self) -> None:
        """Try the queue once, and remember what stopped it."""
        connect = self.connect
        if connect is None:
            return
        problems_when_started = self._problems_noted
        if await asyncio.to_thread(self._retrying) == 0:
            self._note_reachable(problems_when_started)
            return
        self._last_attempt_at = current_instant()
        try:
            async with connect() as client:
                guide = await self._guide_over(client)
                report = await drain_responses(
                    client, self.project, guide.artifacts, import_responses=True, only_held_for_retry=True
                )
        except SpoolLockedError:
            logger.info("capture delivery: another drain holds the spool, so this attempt is skipped")
            return
        except (Dhis2ClientError, httpx2.HTTPError) as error:
            self._note_could_not_reach(str(error))
            return
        if report.stopped is not None:
            self._note_could_not_reach(report.stopped.reason)
            return
        self._note_reachable(problems_when_started)
        for outcome in report.rejected:
            logger.warning("DHIS2 refused capture %s after its submitter had gone", outcome.response_id)

    def status(self) -> DeliveryStatus:
        """What this facade has not landed in DHIS2. It reads the spool, so call it off an event loop."""
        held = self.spool.receipts((ResponseLifecycle.RECEIVED,))
        waiting = self._still_waiting(held)
        retrying = self._still_waiting(tuple(receipt for receipt in held if receipt.held_for_retry))
        rejected = self.spool.count_by_lifecycle().get(ResponseLifecycle.REJECTED, 0)
        return DeliveryStatus(
            delivery=self.mode,
            posts_as=self._actor(),
            waiting=waiting,
            retrying=retrying,
            refused=rejected + (len(held) - waiting),
            last_attempt_at=self._last_attempt_at,
            last_problem=self._last_problem,
        )

    async def _keep_delivering(self) -> None:
        """Try the queue until stopped, sooner when a capture has joined it."""
        while True:
            try:
                await self.attempt()
            except Exception as error:
                # A worker that died here would leave the queue stuck with nothing saying so.
                logger.exception("capture delivery: the attempt failed unexpectedly")
                self._note_could_not_reach(f"{type(error).__name__}: {error}")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.retry_seconds)
            self._wake.clear()

    async def _post(
        self,
        poster: ImportPoster,
        client: Dhis2Client,
        envelope: StoredResponseEnvelope,
    ) -> DeliveryOutcome:
        """Post one capture under whoever `poster` is, and file it only if DHIS2 took it."""
        guide = await self._guide_over(client)
        # The receipt's copy, stamped with its id: that id names the event, so every attempt at it is the same event.
        result = translate_response(QuestionnaireResponse.model_validate(envelope.response), guide.conversion)
        if result.is_refused:
            raise CaptureNotTranslatedError(tuple(refusal.reason for refusal in result.refusals))
        overwritten = await asyncio.to_thread(self._replaced_values, result)
        validate_only = self.mode is CaptureDelivery.VALIDATE_ONLY
        try:
            answer = await post_translation(poster, result, dry_run=validate_only)
        except (Dhis2ClientError, httpx2.HTTPError) as error:
            if validate_only or _never_arrived(error):
                raise
            raise _AnswerLostError(
                str(error) or type(error).__name__, None if result.event is None else result.event.event
            ) from error
        if answer.verdict is ForwardImportVerdict.NONE:
            raise _NoVerdictError(f"DHIS2 answered {answer.http_status} with no verdict about this capture")
        if answer.verdict is ForwardImportVerdict.REJECTED:
            reasons = tuple(issue.line for issue in answer.issues) or (answer.message or "no reason stated",)
            raise CaptureRefusedByInstanceError(answer.http_status or 409, reasons)
        if validate_only:
            return DeliveryOutcome(kind=DeliveryKind.VALIDATED, envelope=envelope, overwritten=overwritten)
        claim = result.completeness if self.project.config.forward.register_completeness else None
        record = ForwardImportRecord(
            **answer.model_dump(),
            target_kind=result.target_kind,
            received_at=envelope.received_at,
            cells=() if result.data_value_set is None else aggregate_cells(result.data_value_set),
            completeness=None if claim is None else await claim_completeness(poster, claim),
        )
        await asyncio.to_thread(self.spool.save_forwarded, envelope, record)
        return DeliveryOutcome(kind=DeliveryKind.SENT, envelope=envelope, overwritten=overwritten)

    async def _could_not_reach(
        self, envelope: StoredResponseEnvelope, problem: str, *, caller: ImportPoster | None
    ) -> DeliveryOutcome:
        """Answer a capture DHIS2 never ruled on: held where this server can try again, and otherwise failed."""
        if caller is not None or self.mode is CaptureDelivery.VALIDATE_ONLY:
            raise UpstreamError(f"DHIS2 could not take this capture, and nothing was stored: send it again ({problem})")
        # Saved before it is noted: a worker that read an empty queue in between would clear the problem it never saw.
        note = HELD_AND_RETRYING if self._worker is not None else self.held_note
        held = await self._held(envelope, note, retrying=self.retries)
        self._note_could_not_reach(problem)
        return held

    async def _answer_lost(
        self, envelope: StoredResponseEnvelope, error: _AnswerLostError, *, caller: ImportPoster | None
    ) -> DeliveryOutcome:
        """Answer a capture that may have landed: never as stored nowhere, or with an invitation to duplicate it."""
        if caller is not None:
            raise CaptureOutcomeUnknownError(error.event_uid) from error
        note = HELD_ANSWER_LOST if self._worker is not None else self.held_note
        held = await self._held(envelope, note, retrying=self.retries, may_have_arrived=True)
        self._note_could_not_reach(str(error))
        return held

    async def _held(
        self, envelope: StoredResponseEnvelope, note: str, *, retrying: bool, may_have_arrived: bool = False
    ) -> DeliveryOutcome:
        """Keep one capture in the queue. It blocks on an fsync, which is why it runs off the event loop."""
        kept = envelope.model_copy(update={"held_for_retry": retrying})
        await asyncio.to_thread(self.spool.save, kept)
        return DeliveryOutcome(kind=DeliveryKind.HELD, envelope=kept, note=note, may_have_arrived=may_have_arrived)

    async def _guide_over(self, client: Dhis2Client) -> _DeliveryGuide:
        """What captures are translated against, read once over the connection the first capture opened."""
        if self._guide is not None:
            return self._guide
        async with self._guide_lock:
            if self._guide is None:
                self._guide = await self._read_guide(client)
        return self._guide

    async def _read_guide(self, client: Dhis2Client) -> _DeliveryGuide:
        """Read the guide off disk where it is compiled and off the instance where it is not, and its value types."""
        if self.artifacts is not None and self.conversion is not None:
            return _DeliveryGuide(artifacts=self.artifacts, conversion=self.conversion)
        try:
            artifacts = load_compiled_artifacts(self.project)
        except CompiledIgMissingError:
            artifacts = await fetch_live_artifacts(client, self.project)
        naming = ConversionNaming.from_config(self.project.config.generate, self.project.config.ig.canonical)
        value_types = await fetch_value_types(client, bound_question_uids(artifacts, naming), progress=StepAnnouncer())
        conversion = build_project_context(
            self.project,
            artifacts,
            value_types_by_data_element=value_types,
            coded_answer_mode=configured_coded_answer_mode(self.project),
        )
        return _DeliveryGuide(artifacts=artifacts, conversion=conversion)

    def _replaced_values(self, result: ConversionResult) -> tuple[OverwrittenValue, ...]:
        """Which values this payload would replace, refusing it whole where the project says so."""
        if result.data_value_set is None:
            return ()
        index = build_forwarded_cell_index(spool_layout(self.project))
        replaced = index.already_sent(aggregate_cells(result.data_value_set))
        if replaced and self.project.config.forward.overwrites is OverwritePosture.REFUSE:
            raise CaptureOverwriteRefusedError(tuple(value.line for value in replaced))
        return replaced

    def _retrying(self) -> int:
        """How many queued captures this server took on sending, leaving out those a drain has already refused."""
        held = self.spool.receipts((ResponseLifecycle.RECEIVED,))
        return self._still_waiting(tuple(receipt for receipt in held if receipt.held_for_retry))

    def _still_waiting(self, held: tuple[StoredReceipt, ...]) -> int:
        """How many of these queued captures no drain has refused."""
        return sum(1 for receipt in held if self.spool.refusal_record(receipt.response_id) is None)

    def _actor(self) -> DeliveryActor:
        """Whose rights DHIS2 applies to what this facade posts."""
        if self.connect is None or self.mode is CaptureDelivery.HOLD:
            return DeliveryActor.NOBODY
        return DeliveryActor.CALLER if self.posts_as_caller else DeliveryActor.CONNECTION

    def _note_could_not_reach(self, problem: str) -> None:
        """Remember that DHIS2 is away, so later captures queue behind the ones waiting and the status can say why."""
        self._queue_owed = True
        self._last_problem = problem
        self._problems_noted += 1
        logger.warning("capture delivery: DHIS2 could not be reached: %s", problem)

    def _note_reachable(self, problems_when_started: int) -> None:
        """Clear the problem, unless a capture met DHIS2 being away while this attempt was running."""
        if self._problems_noted == problems_when_started:
            self._queue_owed = False
            self._last_problem = None


@asynccontextmanager
async def holding_connection(client: Dhis2Client) -> AsyncGenerator[Dhis2Client]:
    """Hand over a connection somebody else opened, and leave it open."""
    yield client
