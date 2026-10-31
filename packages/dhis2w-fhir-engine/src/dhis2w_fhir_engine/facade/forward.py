"""The drain: translate every spooled capture, post it to DHIS2, and file what DHIS2 said."""

from __future__ import annotations

import re
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx2
from dhis2w_client.errors import AuthenticationError, Dhis2ApiError, Dhis2ClientError
from dhis2w_client.generated.v43.oas import (
    DataValueSet,
    ImportConflict,
    ImportSummary,
    TrackerImportError,
    TrackerImportReport,
)
from dhis2w_client.generated.v43.tracker import TrackerEvent
from dhis2w_client.v43.aggregate import CompleteDataSetRegistration, CompleteDataSetRegistrations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dhis2w_fhir_engine.facade.config import CorrectionPosture, FhirProject, OverwritePosture, WithdrawalPosture
from dhis2w_fhir_engine.facade.conversion.artifacts import (
    BoundQuestionUids,
    CompiledArtifacts,
    CompiledIgMissingError,
    ProgramRuleNames,
    bound_question_uids,
    build_project_context,
    compiled_guide_missing_at,
    load_compiled_artifacts,
    program_rule_names,
)
from dhis2w_fhir_engine.facade.conversion.schemas import (
    FORWARD_TARGET_ORDER,
    CodedAnswerMode,
    ConversionNaming,
    ConversionNote,
    ConversionRefusal,
    ConversionRefusalCategory,
    ConversionReport,
    ConversionResult,
    ConversionTargetKind,
)
from dhis2w_fhir_engine.facade.conversion.translator import translate_responses
from dhis2w_fhir_engine.facade.forward_records import (
    ForwardCompletenessKind,
    ForwardCompletenessOutcome,
    ForwardCompletenessRetry,
    ForwardImportIssue,
    ForwardImportOutcome,
    ForwardImportRecord,
    ForwardImportVerdict,
    ForwardRejectionReason,
)
from dhis2w_fhir_engine.facade.overwrite import (
    AggregateCell,
    ForwardedCellIndex,
    ForwardedSubmission,
    ForwardOverwrite,
    OverwrittenValue,
    aggregate_cells,
    build_forwarded_cell_index,
)
from dhis2w_fhir_engine.facade.progress import StepAnnouncer
from dhis2w_fhir_engine.facade.refusals import ServeError
from dhis2w_fhir_engine.facade.spool import (
    ForwardRefusalRecord,
    QuarantinedFile,
    RefusalReason,
    SpooledResponse,
    SpoolLayout,
    SpoolState,
    drain_lock,
    move_to_forwarded,
    move_to_rejected,
    read_import_reports,
    read_received_responses,
    read_refusal_record,
    record_refusal,
    sweep_orphan_temporary_files,
    write_import_report,
)
from dhis2w_fhir_engine.facade.store import COMPILED_RESOURCES_RELATIVE_PATH

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
    from contextlib import AbstractAsyncContextManager

    from dhis2w_client import Dhis2Client
    from dhis2w_client.generated.v43.schemas import DataElement, TrackedEntityAttribute

    from dhis2w_fhir_engine.facade.progress import ProgressReporter


def _uid_filter(uids: list[str]) -> str:
    """The DHIS2 metadata filter selecting exactly the configured UIDs."""
    return f"id:in:[{','.join(uids)}]"


#: How many steps `forward_responses` announces: read the spool, read the guide, read the value types,
#: translate, post, register the completed reports, file what each response became.
FORWARD_STEPS = 7


#: The `/api/dataValueSets` endpoint one aggregate response is imported through.
_DATA_VALUE_SETS_PATH = "/api/dataValueSets"


#: The `/api/completeDataSetRegistrations` endpoint a `completed` aggregate response is registered
#: complete through, once DHIS2 has taken its values.
_COMPLETE_DATA_SET_REGISTRATIONS_PATH = "/api/completeDataSetRegistrations"


#: The `/api/tracker` endpoint both event kinds and every registration are imported through.
_TRACKER_PATH = "/api/tracker"


#: The `/api/tracker` bundle key each payload kind rides under: an event, the tracked entity a
#: registration creates with its enrollment nested inside it, or - for a registration enrolling a
#: person the instance already holds - that enrollment on its own at the top level. The third key
#: is what keeps the person untouched: an enrollment nested in a `trackedEntities` entry needs
#: `CREATE_AND_UPDATE`, and that rewrites the person's owning organisation unit (DHIS2_ISSUES.md 73).
_TRACKER_EVENTS_KEY = "events"


_TRACKER_TRACKED_ENTITIES_KEY = "trackedEntities"


_TRACKER_ENROLLMENTS_KEY = "enrollments"


#: Which of those keys each `/api/tracker` target kind's payload rides under. The aggregate kind is
#: absent because it posts to an endpoint of its own with no bundle around it at all.
_TRACKER_KEYS_BY_TARGET_KIND: dict[ConversionTargetKind, str] = {
    ConversionTargetKind.TRACKED_ENTITY: _TRACKER_TRACKED_ENTITIES_KEY,
    ConversionTargetKind.TRACKER: _TRACKER_TRACKED_ENTITIES_KEY,
    ConversionTargetKind.TRACKER_ENROLLMENT: _TRACKER_ENROLLMENTS_KEY,
    ConversionTargetKind.EVENT: _TRACKER_EVENTS_KEY,
    ConversionTargetKind.TRACKER_EVENT: _TRACKER_EVENTS_KEY,
}


#: What a dry run adds to an aggregate post. v42 spells validate-only on this endpoint as `dryRun`,
#: and the import runs every rule it would run for real while committing nothing.
_DATA_VALUE_SETS_DRY_RUN_PARAMS = {"dryRun": "true"}


#: The parameters every forwarded event is posted under. Every payload names its own receipt-derived
#: uid, and CREATE is what makes a re-forwarded receipt collide loudly instead of updating in place;
#: `async=false` makes the answer the import report itself rather than a job reference to poll.
_TRACKER_PARAMS = {"importStrategy": "CREATE", "async": "false"}

#: What `/api/tracker` answers when told to create a person, an enrollment, or an event it already holds.
_ALREADY_EXISTS_CODES = frozenset({"E1002", "E1030", "E1080"})

#: What a held capture's import report says where an earlier attempt landed and only its answer was lost.
_ALREADY_LANDED_MESSAGE = "DHIS2 already holds this capture: an earlier attempt landed, and its answer never arrived"

#: The status an accepted import report carries.
_ACCEPTED_IMPORT_STATUS = "OK"


#: What a dry run adds to a tracker post. v42 has no `dryRun` on `/api/tracker`; the endpoint's own
#: validate-only mode is `importMode=VALIDATE`, which runs the whole validation pass and persists nothing.
_TRACKER_DRY_RUN_PARAMS = {"importMode": "VALIDATE"}


#: The projection the value-type read asks for - the one fact the compiled IG cannot carry, because
#: R4 spells `BOOLEAN` and `TRUE_ONLY` as the same `#boolean` item type. The same two fields answer
#: for both objects a question is asked from, so one shape serves both reads.
_VALUE_TYPE_FIELDS = "id,valueType"


#: How many UIDs one `id:in:[...]` filter carries before the value-type read is split across requests.
_VALUE_TYPE_BATCH_SIZE = 200


#: The fields only an `/api/dataValueSets` ImportSummary carries, which is how one is recognised
#: whether it arrived inside a `WebMessage.response` or bare.
_DATA_VALUE_SET_REPORT_KEYS = frozenset({"importCount", "conflicts", "responseType", "dataSetComplete"})


#: The fields only an `/api/tracker` TrackerImportReport carries. The endpoint answers a refusal with
#: this document **bare** - no `WebMessage` around it - so recognising it by shape is the whole trick.
_TRACKER_REPORT_KEYS = frozenset({"validationReport", "stats", "bundleReport"})


#: The identifiers DHIS2 embeds in a validation message, quoted or bare. Generalising them is what makes
#: two hundred rejections of one rule roll up into one cause rather than two hundred distinct sentences.
#:
#: DHIS2 quotes some and not others - `E1029` backticks its organisation-unit list where `E8025` states
#: the attribute option combo as eleven bare characters in the same sentence - so a bare DHIS2 UID
#: generalises exactly as a quoted one does. One alternation reads both in a single pass, so a rule name
#: substituted for a UID is never itself re-read as one.
_EMBEDDED_IDENTIFIER = re.compile(r"`[^`]*`|\b[A-Za-z][A-Za-z0-9]{10}\b")


#: What one whole token has to look like to be a DHIS2 UID: eleven characters, a letter first. The
#: bare half of `_EMBEDDED_IDENTIFIER` already reads that shape off a sentence; a quoted run is
#: measured against it token by token, because DHIS2 backticks whole clauses as readily as ids.
_UID_SHAPE = re.compile(r"[A-Za-z][A-Za-z0-9]{10}")


#: What separates two identifiers inside one quoted run. `E1029` and `E8025` backtick a whole
#: organisation-unit list - brackets and all - as one run, so the brackets separate rather than
#: belong, and the run reads as identifiers only when every token between them does.
_QUOTED_TOKEN_SEPARATOR = re.compile(r"[,;\s\[\]]+")


#: Where a bare word turns from lower case to upper. A DHIS2 UID is eleven random characters and a
#: sentence is made of words, so the two are told apart by shape: `DataElement` and `dataElement` are
#: English spelt for a machine and turn once, where `ImspTQPwCqd` turns wherever the generator happened
#: to land. A word of eleven letters turning at most once is left alone, and either way the response's
#: own report carries the sentence exactly as DHIS2 sent it.
_CASE_TURN = re.compile(r"[a-z][A-Z]")


#: How many turns a bare eleven-letter word takes before it is read as a UID rather than as prose.
_CASE_TURNS_OF_A_WORD = 1


#: How often the posting step re-captions itself, so a 300-response drain narrates without one line each.
_POST_TICK_INTERVAL = 10


#: The status at and above which an answer is about the instance rather than about the payload. DHIS2
#: states a verdict on a payload with 409 and its own import report; a 5xx is the instance failing to
#: reach a verdict at all, and reading one as "your payload was refused" would file a receipt under a
#: rejection DHIS2 never made.
_SERVER_ERROR_STATUS = 500


#: The status the client raises an authentication failure on rather than handing the body back.
_UNAUTHORIZED_STATUS = 401


#: The two codes DHIS2 answers a tracker event whose enrollment it cannot find with: `E1313` for the
#: enrollment nobody has, and the `E1079` program mismatch it asserts against that same absent
#: enrollment (DHIS2_ISSUES.md 68). A rejection carrying only these is the whole shape a dry run cannot check.
_ABSENT_ENROLLMENT_ERROR_CODES = frozenset({"E1079", "E1313"})


#: What a dry run can and cannot say about a completeness registration it did not make. The endpoint
#: has a `dryRun` of its own, but a dry run wrote no values for it to be a claim about, so the honest
#: statement is what would be registered rather than a validation of the tuple.
_COMPLETENESS_DRY_RUN_REASON = (
    "A dry run writes no values, so there is nothing for a completeness registration to be a claim about "
    "and none is posted. What the run states is the tuple each `completed` response would register - the "
    "data set, period, organisation unit, and attribute option combo its values ride under. Whether DHIS2 "
    "accepts that write is checked by the import, which registers only after it has taken the values."
)


#: What a dry run says about a stage event whose enrollment only a registration of the same run creates.
_UNVERIFIABLE_IN_DRY_RUN_REASON = (
    "The enrollment this event answers into is created by a registration validated in the same run. A dry "
    "run writes nothing to the instance, so there is no enrollment for DHIS2 to check the event against. "
    "An import posts registrations first, and the event is checked against the enrollment one created."
)


class ImportPoster(Protocol):
    """What posting a payload needs of a connection: the facade's own client, or one bound to a caller's credentials."""

    async def post_raw(self, path: str, body: Any = None, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST one document, answering parsed JSON and raising `Dhis2ApiError` for a refusal."""
        ...


class ForwardOutcomeKind(StrEnum):
    """What became of one spooled response in a forward run."""

    #: The response never reached DHIS2, and the receipt stays in `received/` for the next drain to
    #: retry - a committing drain writing its refusal record beside it so the listing can say so.
    #: Two things refuse a response. The translator would not read it whole, which is the ordinary
    #: case, and is terminal where the refusal is one nothing can fix - see
    #: `TERMINAL_REFUSAL_CATEGORIES` - in which case an import files it to `rejected/` instead. Or
    #: the drain runs under `[forward] overwrites = "refuse"` and the payload holds an aggregate
    #: value a forwarded receipt already sent, which `ForwardOutcome.overwrite_refused` says.
    REFUSED = "refused"

    #: DHIS2 took the payload - imported it, or validated it on a dry run.
    ACCEPTED = "accepted"

    #: DHIS2 was given the payload and refused it; the import report says why.
    REJECTED = "rejected"

    #: A dry run could not check the payload, because what it answers into is created by the same run.
    UNVERIFIABLE = "unverifiable"

    #: The drain stopped before this receipt's turn, so DHIS2 was never asked about it and it stays put.
    NOT_POSTED = "not-posted"


#: The refusal categories that no change to the guide and no change to the instance could ever
#: resolve, so a receipt carrying one is filed to `rejected/` instead of being retried by every drain
#: for the rest of the project's life. The discriminator is stated as a set rather than as a flag on
#: a refusal, because membership is a doctrine about what this toolchain builds rather than a
#: property of the response: `entered-in-error` asks for a withdrawal, withdrawal is a deletion, and
#: this toolchain imports - see `docs/design/data-lifecycle.md`. Every other refusal has a fix
#: somewhere, so every other refusal stays in the queue.
TERMINAL_REFUSAL_CATEGORIES = frozenset({ConversionRefusalCategory.ENTERED_IN_ERROR_IS_A_DELETION})


#: What the refusal record beside an overwrite-refused receipt calls the refusal. A category of its
#: own rather than a `ConversionRefusalCategory`, because the translator read this response whole -
#: what refused it is the spool's own record of what this project has already sent.
OVERWRITE_REFUSAL_CATEGORY = "overwrite-refused"


#: What the sidecar of a terminally refused receipt states in place of a DHIS2 answer. DHIS2 was
#: never asked, so the status is the forwarder's own word for what happened.
_TERMINAL_REFUSAL_STATUS = "REFUSED"


#: The doctrine the sidecar of a terminally refused receipt writes down, so a person reading
#: `rejected/` cold learns why the receipt is there without a drain report in front of them.
_TERMINAL_REFUSAL_MESSAGE = (
    "The translator will never convert this response, whatever changes in the guide or in the data, so it is "
    "filed here rather than retried by every drain. A drain imports; retracting what one already imported is "
    "`d2w fhir withdraw <response id>`, naming the forwarded receipt to take back rather than this one - see "
    "docs/design/data-lifecycle.md. `d2w fhir requeue` puts the receipt back in the queue for an operator "
    "who wants it tried again."
)


class ForwardEndpointAnswer(BaseModel):
    """One import endpoint's HTTP answer, as the projection that reads it is handed it.

    The body is a raw JSON object because the two endpoints disagree about what wraps their report,
    and each projection unwraps its own - so this is the parse boundary, and the dict goes no
    further than the projection that reads it. The status rides beside it because a body alone
    cannot say whether the endpoint answered at all: an authentication gateway's `403` and DHIS2's
    own `409` both arrive as JSON objects.

    `status_code` is None for an answer the client did not raise on, which is every 2xx. The client
    parses a successful body without carrying its status back, and no drain acts on the difference
    between a 200 and a 201 - a stop names the status only when there was a failing one to name.
    """

    model_config = ConfigDict(frozen=True)

    body: dict[str, Any]
    status_code: int | None = None


class ForwardUnverifiableReason(BaseModel):
    """One cause a dry run could not check a payload against, and how many responses met it.

    Separate from `ForwardRejectionReason` because it is a different claim about the run: a rejection
    says the payload is wrong, and this says the run could not tell either way.
    """

    model_config = ConfigDict(frozen=True)

    reason: str
    responses: int


class ForwardStop(BaseModel):
    """Why a drain stopped posting before it had been through everything it translated.

    A stop is the instance failing rather than a payload being refused: a 5xx, or a connection that
    never completed. Neither says anything about the receipt that met it, so the receipt is left in
    the queue for the next drain along with everything behind it - and the run says so, because a
    drain that quietly posted half a spool and reported success is the one failure mode that costs
    data the operator does not know to go looking for.
    """

    model_config = ConfigDict(frozen=True)

    response_id: str
    """The receipt whose post met the failure, which is the one the next drain tries first."""

    status_code: int | None = None
    """The HTTP status the instance answered with, or None when the request never got an answer."""

    reason: str


class ForwardFilingIssue(BaseModel):
    """One receipt whose file was already gone when the drain went to move it.

    A lost race rather than a failure: something else - a second operator, a hand-run `mv` - moved
    the file between the read that listed it and the rename that would have filed it. DHIS2 has
    already answered about the payload either way, so the answer is kept and the run says the file
    was not where it left it, rather than aborting a drain that has done its work.
    """

    model_config = ConfigDict(frozen=True)

    response_id: str
    reason: str


class ForwardOutcome(BaseModel):
    """What one spooled response became: a DHIS2 import answer, or the reasons it never reached DHIS2."""

    model_config = ConfigDict(frozen=True)

    response_id: str
    questionnaire: str | None = None
    target_kind: ConversionTargetKind | None = None
    kind: ForwardOutcomeKind
    notes: tuple[ConversionNote, ...] = ()
    refusals: tuple[ConversionRefusal, ...] = ()
    import_outcome: ForwardImportOutcome | None = None
    completeness: ForwardCompletenessOutcome | None = None
    """What became of an aggregate response's completeness claim; unset for every other payload kind."""

    overwritten_values: tuple[OverwrittenValue, ...] = ()
    """Every value this response sent that a forwarded receipt had already sent, and which receipt sent it.

    Carried only where DHIS2 took the payload, because a payload the instance refused replaced
    nothing. A dry run carries them as the prediction they are: what an import of this spool would
    replace, stated while there is still something to be done about it. Under
    `[forward] overwrites = "refuse"` they are what the drain refused over, and nothing was sent.
    """

    overwrite_refused: bool = False
    """Whether the drain would not post this payload because it holds a value an earlier submission sent.

    Only ever true under `[forward] overwrites = "refuse"`, and only of an aggregate payload. The
    values it was refused over are `overwritten_values`, and the receipt is still in the queue.
    """

    spool_path: str
    """Where the receipt sits now, relative to the project root - unmoved on a dry run and on a refusal."""

    submitted_by: str | None = None
    """The DHIS2 username the facade validated the capture under, carried through from the receipt.

    Facade-side provenance and nothing more. The values this drain posts reach DHIS2 as the
    forwarding profile, and `storedBy` on the instance is DHIS2's own stamp of that profile - so the
    receipt is where "who captured this" is answered, and this field is that answer travelling.
    """


class ForwardReport(BaseModel):
    """The outcome of draining one project's capture spool into DHIS2, in the order it was drained.

    `outcomes` is in spool order, which is not the posting order: payloads go to DHIS2 in
    `FORWARD_TARGET_ORDER`, people first and then the payloads that create an enrollment, so a
    person exists before a registration of the same drain enrols them and an enrollment exists
    before the stage events of the same drain answer against it. The two orders are deliberately
    separate - a report reads back as the spool it drained, and the posting order is a fact about
    the run rather than about any one receipt.
    """

    model_config = ConfigDict(frozen=True)

    project_root: Path
    dry_run: bool
    coded_answer_mode: CodedAnswerMode
    register_completeness: bool = True
    """Whether the run registered completeness for the `completed` aggregate responses DHIS2 took."""

    overwrite_posture: OverwritePosture = OverwritePosture.ALLOW
    """What the run did with an aggregate value a forwarded receipt already sent - post it, or refuse it."""

    correction_posture: CorrectionPosture = CorrectionPosture.OFF
    """What this project does with a submission that names a receipt it corrects, as the run resolved it.

    Stated rather than acted on by the drain, because the two dials that govern a marked submission
    are the deployment's posture rather than one run's: an operator reading a report has to be able
    to see which posture the drain ran under without opening `fhir.toml` beside it.
    """

    withdrawal_posture: WithdrawalPosture = WithdrawalPosture.OFF
    """Whether this project retracts what it forwarded, which is what `d2w fhir withdraw` requires."""

    spooled: int = 0
    outcomes: tuple[ForwardOutcome, ...] = ()
    unreadable_artifacts: tuple[str, ...] = ()
    """Every published non-form document the R4 models could not read, and so translated against nothing."""

    stopped: ForwardStop | None = None
    """Why the drain stopped short, when it did; None is a run that was through the whole spool."""

    quarantined: tuple[QuarantinedFile, ...] = ()
    """Every file in `received/` that would not read as a receipt, moved to `malformed/` and named here."""

    filing_issues: tuple[ForwardFilingIssue, ...] = ()
    """Every receipt whose file had already moved when the drain went to file it."""

    program_rule_names: ProgramRuleNames = Field(default_factory=ProgramRuleNames)
    """The rules the guide publishes, which is what lets a refusal name the rule rather than a UID."""

    completeness_retries: tuple[ForwardCompletenessRetry, ...] = ()
    """Every completeness claim an earlier drain left owed that this drain posted again.

    Values already in DHIS2 whose report was never registered complete. The claim rides in the
    forwarded receipt's own sidecar, so a drain finds it there rather than by re-posting values.
    """

    forwarded_without_values: int = 0
    """Receipts DHIS2 accepted whose sidecar records no values, so this run cannot say what they sent.

    Zero for every spool this toolchain filled, since a drain records an aggregate payload's cells as
    it files the receipt. A count above zero is a spool something else wrote into, and it is stated
    rather than swallowed: those receipts are the ones an overwrite could hide behind.
    """

    @property
    def refused(self) -> tuple[ForwardOutcome, ...]:
        """Every response this run would not send, which is every response that stayed put in the queue."""
        return tuple(outcome for outcome in self.outcomes if outcome.kind == ForwardOutcomeKind.REFUSED)

    @property
    def translator_refused(self) -> tuple[ForwardOutcome, ...]:
        """Every response the translator would not read whole, which is the refusal a guide or a fix answers."""
        return tuple(outcome for outcome in self.refused if not outcome.overwrite_refused)

    @property
    def overwrite_refused(self) -> tuple[ForwardOutcome, ...]:
        """Every response this run would not send because it holds a value an earlier submission already sent.

        Empty under `[forward] overwrites = "allow"`, which posts such a value and names it.
        """
        return tuple(outcome for outcome in self.refused if outcome.overwrite_refused)

    @property
    def accepted(self) -> tuple[ForwardOutcome, ...]:
        """Every response DHIS2 took."""
        return tuple(outcome for outcome in self.outcomes if outcome.kind == ForwardOutcomeKind.ACCEPTED)

    @property
    def rejected(self) -> tuple[ForwardOutcome, ...]:
        """Every response DHIS2 was given and refused."""
        return tuple(outcome for outcome in self.outcomes if outcome.kind == ForwardOutcomeKind.REJECTED)

    @property
    def unverifiable(self) -> tuple[ForwardOutcome, ...]:
        """Every response this dry run could not check, because the run itself would create what it needs."""
        return tuple(outcome for outcome in self.outcomes if outcome.kind == ForwardOutcomeKind.UNVERIFIABLE)

    @property
    def not_posted(self) -> tuple[ForwardOutcome, ...]:
        """Every response the drain stopped short of, which is every response still waiting its turn."""
        return tuple(outcome for outcome in self.outcomes if outcome.kind == ForwardOutcomeKind.NOT_POSTED)

    @property
    def translated_count(self) -> int:
        """How many responses produced a payload.

        An overwrite-refused response produced one and was then not sent, so it counts as translated
        and as refused both - the two numbers answer different questions about the same receipt.
        """
        return self.spooled - len(self.translator_refused)

    @property
    def posted_count(self) -> int:
        """How many payloads were posted to DHIS2."""
        return len(self.accepted) + len(self.rejected) + len(self.unverifiable)

    @property
    def counts_line(self) -> str:
        """The whole run in one line, which is what a progress reporter and a summary hint both want.

        The not-posted clause is stated only by a run that stopped short. It is not a count every run
        has a zero for - it is the shape of a run that ended early, and a reader who sees it needs to
        read no further to know the spool still holds work. The unverifiable clause is stated the same
        way - only by a dry run that had any, since only a dry run can produce the outcome - and the
        mode rides the posted count, so a committed line can never claim a dry run.

        The refused count is every response that stayed in the queue, whether the translator would
        not read it or the run would not overwrite what it holds. One number, because the operator
        reading this line is asking how much of the spool is still waiting; which refusal each one
        met is the report's business, not the summary's.
        """
        posted = f"{self.posted_count:,} posted (validate only)" if self.dry_run else f"{self.posted_count:,} posted"
        line = (
            f"{self.spooled:,} spooled, {self.translated_count:,} translated, {len(self.refused):,} refused, "
            f"{posted}, {len(self.accepted):,} accepted, {len(self.rejected):,} rejected"
        )
        if self.unverifiable:
            line += f", {len(self.unverifiable):,} unverifiable"
        return f"{line}, {len(self.not_posted):,} not posted" if self.stopped is not None else line

    @property
    def completeness_outcomes(self) -> tuple[ForwardCompletenessOutcome, ...]:
        """What every aggregate response of the run said about completeness, in spool order."""
        return tuple(outcome.completeness for outcome in self.outcomes if outcome.completeness is not None)

    def completeness_of(self, kind: ForwardCompletenessKind) -> tuple[ForwardCompletenessOutcome, ...]:
        """Every completeness outcome of one kind, which is what the counts and the report sections read."""
        return tuple(outcome for outcome in self.completeness_outcomes if outcome.kind == kind)

    @property
    def completeness_line(self) -> str:
        """The run's completeness in one line, or nothing at all when no aggregate response claimed any."""
        outcomes = self.completeness_outcomes
        if not outcomes:
            return ""
        counted = Counter(outcome.kind for outcome in outcomes)
        return ", ".join(f"{counted[kind]:,} {kind}" for kind in ForwardCompletenessKind if counted[kind])

    @property
    def completeness_retry_line(self) -> str:
        """The claims earlier drains left owed in one line, or nothing at all when this run met none."""
        if not self.completeness_retries:
            return ""
        counted = Counter(retry.outcome.kind for retry in self.completeness_retries)
        stated = ", ".join(f"{counted[kind]:,} {kind}" for kind in ForwardCompletenessKind if counted[kind])
        return f"{len(self.completeness_retries):,} claim(s) owed by an earlier drain: {stated}"

    @property
    def overwrites(self) -> tuple[ForwardOverwrite, ...]:
        """Every response of the run that sent a value an earlier submission had already sent, in spool order."""
        return tuple(
            ForwardOverwrite(response_id=outcome.response_id, values=outcome.overwritten_values)
            for outcome in self.outcomes
            if outcome.overwritten_values
        )

    @property
    def overwritten_value_count(self) -> int:
        """How many values of the run an earlier submission had already sent."""
        return sum(len(outcome.overwritten_values) for outcome in self.outcomes)

    @property
    def overwrite_line(self) -> str:
        """The run's replaced values in one line, or nothing at all when it sent none a receipt had sent."""
        overwrites = self.overwrites
        if not overwrites:
            return ""
        return f"{self.overwritten_value_count:,} value(s) across {len(overwrites):,} response(s)"

    @property
    def completeness_dry_run_reason(self) -> str:
        """What this dry run could not check about the completeness it would register, or nothing at all."""
        if not self.dry_run or not self.completeness_of(ForwardCompletenessKind.WOULD_REGISTER):
            return ""
        return _COMPLETENESS_DRY_RUN_REASON

    @property
    def rejection_reasons(self) -> tuple[ForwardRejectionReason, ...]:
        """Every rejection of the run rolled up by cause, commonest first, so a wall of them reads as a few.

        A response counts once per distinct cause it met, however many rows named that cause, so
        `E1029` against two different pairs of objects is one cause of the run rather than two. The
        message shown for a cause is the first one the run met.

        A cause that ended one response keeps its identifiers, and so does a cause whose every
        response met it in the very same words. The generalisation exists so twenty rejections
        differing only in the object they name read as one row; five byte-identical messages differ
        in nothing, so eliding them removes the sentence and buys the reader nothing back. A UID
        naming a program rule the guide published is read back as that rule's name at either count.
        """
        counted: Counter[tuple[str | None, str]] = Counter()
        samples: dict[tuple[str | None, str], str] = {}
        verbatim: dict[tuple[str | None, str], str] = {}
        identical: dict[tuple[str | None, str], bool] = {}
        for outcome in self.rejected:
            imported = outcome.import_outcome
            issues = imported.issues if imported is not None else ()
            causes: dict[tuple[str | None, str], str] = {}
            spoken: dict[tuple[str | None, str], str] = {}
            for issue in issues:
                reason = _generalised_reason(issue.reason, self.program_rule_names)
                key = _rejection_cause_key(issue.error_code, reason)
                causes.setdefault(key, reason)
                spoken.setdefault(key, _generalised_reason(issue.reason, self.program_rule_names, elide=False))
            if not causes:
                message = (imported.message or "DHIS2 gave no reason") if imported is not None else ""
                causes[(None, message)] = message
                spoken[(None, message)] = message
            counted.update(causes.keys())
            for key, reason in causes.items():
                samples.setdefault(key, reason)
                if key in verbatim:
                    identical[key] = identical[key] and verbatim[key] == spoken[key]
                else:
                    verbatim[key] = spoken[key]
                    identical[key] = True
        ordered = sorted(counted.items(), key=lambda item: (-item[1], item[0][0] or "", item[0][1]))
        return tuple(
            ForwardRejectionReason(
                error_code=key[0],
                reason=verbatim[key] if identical[key] else samples[key],
                responses=responses,
            )
            for key, responses in ordered
        )

    @property
    def unverifiable_reasons(self) -> tuple[ForwardUnverifiableReason, ...]:
        """What the run could not check and why, as the section a reader acts on without knowing DHIS2 codes."""
        if not self.unverifiable:
            return ()
        return (
            ForwardUnverifiableReason(
                reason=_UNVERIFIABLE_IN_DRY_RUN_REASON,
                responses=len(self.unverifiable),
            ),
        )


def _compiled_artifacts_or_none(
    project: FhirProject, *, registry_package: Path | None = None
) -> CompiledArtifacts | None:
    """The project's compiled guide, or None when it holds none and the live build stands in for it.

    Absence of the compiled tree is the whole trigger - a project that has run SUSHI reads its own
    build, and one that never has had nothing to read. `[forward] live = false` turns the stand-in
    off, and the refusal naming the two commands that produce a build is then what a drain answers.
    """
    try:
        return load_compiled_artifacts(project, registry_package=registry_package)
    except CompiledIgMissingError:
        if not project.config.forward.live:
            raise
        return None


def _artifacts_completion(artifacts: CompiledArtifacts) -> str:
    """What one guide amounted to, however it was read: the resources kept, the forms among them."""
    unreadable = f", {len(artifacts.unreadable_resources):,} unreadable" if artifacts.unreadable_resources else ""
    return f"{artifacts.resource_count:,} resource(s), {len(artifacts.questionnaires):,} form(s){unreadable}"


async def drain_spool(
    project: FhirProject,
    *,
    connect: Callable[[], AbstractAsyncContextManager[Dhis2Client]],
    read_guide: Callable[[], CompiledArtifacts | None],
    build_guide: Callable[[Dhis2Client, StepAnnouncer], Awaitable[CompiledArtifacts]] | None,
    mode: CodedAnswerMode,
    import_responses: bool,
    register_completeness: bool,
    overwrites: OverwritePosture,
    corrections: CorrectionPosture,
    withdrawals: WithdrawalPosture,
    reporter: ProgressReporter | None,
    only_held_for_retry: bool = False,
) -> ForwardReport:
    """Run one drain, with the spool lock already held - the body `forward_responses` documents.

    `read_guide` answers the guide off disk, or None for a project holding no compiled one, and
    `build_guide` builds it over the open connection in that case. `connect` is opened only once
    the spool holds something to post, so an empty drain against a large instance reads none of it.
    """
    progress = StepAnnouncer(reporter, FORWARD_STEPS)
    progress.step("spool", "reading the capture spool")
    layout = spool_layout(project)
    sweep_orphan_temporary_files(layout)
    reading = read_received_responses(layout)
    spooled = tuple(entry for entry in reading.responses if entry.held_for_retry or not only_held_for_retry)
    quarantined_note = f", {len(reading.quarantined):,} moved to malformed/" if reading.quarantined else ""
    progress.complete(f"{len(spooled):,} pending response(s){quarantined_note}")

    compiled = read_guide()
    if compiled is not None:
        progress.step("guide", "reading the published guide")
        progress.complete(_artifacts_completion(compiled))

    naming = ConversionNaming.from_config(project.config.generate, project.config.ig.canonical)
    dry_run = not import_responses
    owed = _owed_completeness_claims(layout) if register_completeness and not dry_run else ()
    if not spooled:
        # Nothing to translate ends the run here, before a client is opened. The reads past this
        # point exist to translate receipts - the guide off the instance when this project holds no
        # compiled one, then the value type of every question the forms bind - and against a large
        # instance that is thousands of resources fetched to answer a report that says zero. The
        # spool is read first and the compiled guide off disk, so a receipt this run cannot read
        # still raises, and a project with no build still hears about it.
        retries: tuple[ForwardCompletenessRetry, ...] = ()
        if owed:
            progress.step("completeness", "registering the reports an earlier drain left owed")
            async with connect() as connection:
                retries = await _retry_owed_completeness(connection, layout, owed, progress)
            progress.complete(_completeness_retry_completion(retries))
        report = ForwardReport(
            project_root=project.project_root,
            dry_run=dry_run,
            coded_answer_mode=mode,
            register_completeness=register_completeness,
            overwrite_posture=overwrites,
            correction_posture=corrections,
            withdrawal_posture=withdrawals,
            unreadable_artifacts=() if compiled is None else compiled.unreadable_resources,
            quarantined=reading.quarantined,
            completeness_retries=retries,
        )
        progress.complete(report.counts_line)
        return report
    async with connect() as client:
        if compiled is None:
            if build_guide is None:
                raise compiled_guide_missing_at(project.ig_directory / COMPILED_RESOURCES_RELATIVE_PATH)
            progress.step("guide", "building the guide off the instance, this project holding no compiled one")
            artifacts = await build_guide(client, progress)
            progress.complete(_artifacts_completion(artifacts))
        else:
            artifacts = compiled
        bound = bound_question_uids(artifacts, naming)

        progress.step("value types", "reading the value types the forms bind")
        value_types = await fetch_value_types(client, bound, progress=progress)
        progress.complete(f"{len(value_types):,} of {bound.total:,} question object(s) typed")

        context = build_project_context(
            project,
            artifacts,
            value_types_by_data_element=value_types,
            coded_answer_mode=mode,
        )
        progress.step("translate", "translating the spooled responses")
        conversion = translate_responses([entry.response for entry in spooled], context)
        progress.complete(f"{len(conversion.translated):,} translated, {len(conversion.refused):,} refused")

        terminal = _file_terminal_refusals(spooled, conversion, moving=import_responses)
        _record_refusals(spooled, conversion, moving=import_responses)
        overwrite_index = _forwarded_cell_index(layout, conversion)

        progress.step("post", _post_caption(0, len(conversion.translated), dry_run=dry_run))
        posted = await _post_translations(
            client,
            spooled,
            conversion,
            dry_run=dry_run,
            moving=import_responses,
            overwrite_index=overwrite_index,
            overwrites=overwrites,
            registering=register_completeness,
            progress=progress,
        )
        stopped_note = f", stopped: {posted.stopped.reason}" if posted.stopped is not None else ""
        refused_note = (
            f", {len(posted.overwrite_refused):,} refused as an overwrite" if posted.overwrite_refused else ""
        )
        progress.complete(
            f"{len(posted.imports):,} payload(s) posted{' (validate only)' if dry_run else ''}"
            f"{refused_note}{stopped_note}"
        )

        progress.step("completeness", "registering the completed reports")
        completeness_retries = await _retry_owed_completeness(client, layout, owed, progress)
        completeness = await _register_completeness(
            client,
            layout,
            spooled,
            conversion,
            posted.imports,
            dry_run=dry_run,
            registering=register_completeness,
            overwrite_refused=posted.overwrite_refused,
            progress=progress,
        )
        retry_note = _completeness_retry_note(completeness_retries)
        progress.complete(
            _completeness_completion(completeness, dry_run=dry_run, registering=register_completeness) + retry_note
        )

    progress.step("spool", "stating what each response became")
    minted_enrollments = _minted_enrollment_uids(conversion) if dry_run else frozenset[str]()
    filed = {**terminal.paths, **posted.filed}
    outcomes = _collect_outcomes(
        spooled,
        conversion,
        posted.imports,
        completeness,
        project.project_root,
        filed=filed,
        minted_enrollments=minted_enrollments,
        overwritten=posted.overwritten,
        overwrite_refused=posted.overwrite_refused,
    )
    report = ForwardReport(
        project_root=project.project_root,
        dry_run=dry_run,
        coded_answer_mode=mode,
        register_completeness=register_completeness,
        overwrite_posture=overwrites,
        correction_posture=corrections,
        withdrawal_posture=withdrawals,
        spooled=len(spooled),
        outcomes=outcomes,
        unreadable_artifacts=artifacts.unreadable_resources,
        stopped=posted.stopped,
        quarantined=reading.quarantined,
        filing_issues=(*terminal.issues, *posted.filing_issues),
        program_rule_names=program_rule_names(artifacts, naming),
        completeness_retries=completeness_retries,
        forwarded_without_values=overwrite_index.receipts_without_values,
    )
    progress.complete(report.counts_line)
    return report


def _forwarded_cell_index(layout: SpoolLayout, conversion: ConversionReport) -> ForwardedCellIndex:
    """Read what this spool has already landed in DHIS2, and only when this drain could land on it again.

    A drain carrying no aggregate payload cannot replace an aggregate value, so it reads nothing at
    all - `forwarded/` is unbounded, and a tracker run has no reason to pay for a directory it cannot
    collide with. A drain that does carry one pays a single pass: see `dhis2w_fhir_engine.facade.overwrite`.
    """
    if not any(result.payload_of(DataValueSet) is not None for result in conversion.translated):
        return ForwardedCellIndex()
    return build_forwarded_cell_index(layout)


def configured_coded_answer_mode(project: FhirProject) -> CodedAnswerMode:
    """The coded-answer dial a project forwards under, which is the one it captures under."""
    return CodedAnswerMode.STRICT if project.config.serve.strict_codes else CodedAnswerMode.LENIENT


def spool_layout(project: FhirProject) -> SpoolLayout:
    """Where this project's receipts live, off the `[serve] spool_dir` the server writes them under."""
    return SpoolLayout.resolve(project.project_root, project.config.serve.spool_dir)


async def fetch_value_types(
    client: Dhis2Client,
    bound: BoundQuestionUids,
    *,
    progress: StepAnnouncer,
) -> dict[str, str]:
    """Read the DHIS2 value type of every object the published forms ask a question from, in id-only batches.

    Two reads over one table. A link id names a data element on three form kinds and a tracked
    entity attribute on the registration one, and the two live behind different endpoints - but a
    UID identifies exactly one DHIS2 object, so both answers land in the single
    `value_types_by_data_element` map the translation context takes.
    """
    value_types: dict[str, str] = {}
    for batch in _uid_batches(bound.data_element_uids):
        progress.tick(_value_type_caption(len(value_types) + len(batch), bound.total))
        data_elements: list[DataElement] = await client.resources.data_elements.list(
            fields=_VALUE_TYPE_FIELDS, filters=[_uid_filter(batch)], paging=False
        )
        for data_element in data_elements:
            if data_element.id and data_element.valueType is not None:
                value_types[data_element.id] = data_element.valueType.value
    for batch in _uid_batches(bound.tracked_entity_attribute_uids):
        progress.tick(_value_type_caption(len(value_types) + len(batch), bound.total))
        attributes: list[TrackedEntityAttribute] = await client.resources.tracked_entity_attributes.list(
            fields=_VALUE_TYPE_FIELDS, filters=[_uid_filter(batch)], paging=False
        )
        for attribute in attributes:
            if attribute.id and attribute.valueType is not None:
                value_types[attribute.id] = attribute.valueType.value
    return value_types


def _uid_batches(uids: Sequence[str]) -> list[list[str]]:
    """Split one read's UIDs into the batches an `id:in:[...]` filter carries without growing unbounded."""
    return [list(uids[start : start + _VALUE_TYPE_BATCH_SIZE]) for start in range(0, len(uids), _VALUE_TYPE_BATCH_SIZE)]


def _value_type_caption(read: int, total: int) -> str:
    """The live caption the value-type step re-writes itself with as the two reads drain."""
    return f"reading value types ({min(read, total):,}/{total:,})"


def _post_caption(posted: int, total: int, *, dry_run: bool) -> str:
    """The live caption the posting step re-writes itself with as the batch drains."""
    verb = "validating" if dry_run else "importing"
    return f"{verb} payloads ({posted:,}/{total:,})"


class _FiledReceipts(BaseModel):
    """Where the receipts one pass filed now sit, and every move that found nothing to move."""

    model_config = ConfigDict(frozen=True)

    paths: dict[str, Path] = Field(default_factory=dict)
    """Where each filed receipt landed, keyed by response id; a receipt that stayed put is absent."""

    issues: tuple[ForwardFilingIssue, ...] = ()


class _PostedPayloads(BaseModel):
    """What one drain's posting pass got through, and why it stopped if it did not get through it all."""

    model_config = ConfigDict(frozen=True)

    imports: dict[str, ForwardImportOutcome] = Field(default_factory=dict)
    """DHIS2's answer per receipt, holding an entry only for the receipts that were actually posted."""

    filed: dict[str, Path] = Field(default_factory=dict)
    """Where each posted receipt was filed as its verdict arrived; empty on a dry run, which moves nothing."""

    overwritten: dict[str, tuple[OverwrittenValue, ...]] = Field(default_factory=dict)
    """Per receipt, the values it sent that an earlier submission had already sent; absent where it sent none."""

    overwrite_refused: frozenset[str] = frozenset()
    """Which receipts the pass would not post at all, because `overwrites = "refuse"` and they held one."""

    filing_issues: tuple[ForwardFilingIssue, ...] = ()
    stopped: ForwardStop | None = None


async def _post_translations(
    client: Dhis2Client,
    spooled: Sequence[SpooledResponse],
    conversion: ConversionReport,
    *,
    dry_run: bool,
    moving: bool,
    overwrite_index: ForwardedCellIndex,
    overwrites: OverwritePosture,
    registering: bool,
    progress: StepAnnouncer,
) -> _PostedPayloads:
    """Post every translated payload one at a time, filing each receipt as soon as DHIS2 answers about it.

    One payload per POST is what makes the outcome attributable: DHIS2 answers a bundle with one
    report for the bundle, and a spool whose receipts move individually needs one answer each.

    The order is `FORWARD_TARGET_ORDER` first, which is what makes one drain internally consistent:
    a person-only capture creates the person a registration captured seconds later enrols, a
    registration creates the enrollment a stage event captured seconds later answers against, and
    DHIS2 refuses an event naming an enrollment it cannot find with `E1313`. Posting by kind is the
    whole of the coordination - there is no dependency graph, and a registration DHIS2 rejects still
    leaves its stage events to fail `E1313`, which the next drain retries once the cause is fixed.

    Inside one kind the order is arrival, `received_at` then the receipt id. DHIS2 replaces an
    aggregate value in place, so the instance holds whatever was posted last, and posting the oldest
    submission of a cell last would leave it holding the oldest number. Arrival order is also what
    `dhis2w_fhir_engine.facade.overwrite` reads the forwarded index by, so the drain and the index name the same
    receipt as the sender of each cell.

    **The receipt is filed the instant its verdict is known**, sidecar first and then the rename, so
    the disk agrees with DHIS2 at every point of the loop rather than only at the end of it. A drain
    that is killed, loses its terminal, or meets an unwell instance leaves everything it posted in
    `forwarded/` or `rejected/` with the report beside it, and everything it had not reached
    untouched in `received/`. A dry run files nothing, because it wrote nothing.

    **An answer that reaches no verdict stops the drain too.** A receipt only leaves `received/` on
    the endpoint's own import report - the shape only `/api/dataValueSets` or `/api/tracker` answers
    with. A `401`, a `403`, a `404`, a `429`, or a success carrying some other document is whatever
    stands in front of the instance answering instead of it, and filing a receipt on one would drop
    a submission out of the queue that DHIS2 never saw. So the drain stops naming the status, the
    receipt keeps its place, and the next drain posts it again.

    **An instance that fails mid-drain stops the drain.** A 5xx or a connection that never completed
    is the instance being unwell, not a verdict on the payload that met it, and the two things that
    must not happen next are posting the remaining two hundred payloads into it and filing the
    receipt that met it as though DHIS2 had refused it. So the pass stops where it is and answers
    with the outcomes it did get; the receipts already filed stay filed, everything from the failure
    onwards is untouched in `received/`, and the report names what stopped it.

    **An aggregate payload is read against what this spool has already landed, before it is sent.**
    DHIS2 replaces a value it already holds without saying so, so the index answers the question the
    import summary cannot: which of these values a forwarded receipt already sent, and which one. The
    reading is taken before the post and kept only where DHIS2 took the payload - a refused payload
    replaced nothing - and each taken payload then joins the index, so two captures of one value
    inside a single drain are the same finding as two captures a week apart.

    **Under `overwrites = "refuse"` such a payload is not sent at all, and the whole response is what
    is refused.** A payload holding one covered value among ten fresh ones is still one submission,
    and posting the nine while refusing the one would tear a single form across two postures and
    leave the instance holding a report nobody filled in. So the response is refused whole, it keeps
    its place in `received/`, and a committing run writes the refusal record beside it. Nothing joins
    the index either: a payload that was never sent landed nothing.
    """
    translated = sorted(
        ((entry, result) for entry, result in zip(spooled, conversion.results, strict=True) if not result.is_refused),
        key=lambda pair: (_post_order(pair[1]), pair[0].received_at, pair[0].response_id),
    )
    imports: dict[str, ForwardImportOutcome] = {}
    filed: dict[str, Path] = {}
    overwritten: dict[str, tuple[OverwrittenValue, ...]] = {}
    refused: set[str] = set()
    issues: list[ForwardFilingIssue] = []
    refused_at = _utc_instant()
    for posted, (entry, result) in enumerate(translated, start=1):
        envelope = result.payload_of(DataValueSet)
        cells = aggregate_cells(envelope) if envelope is not None else ()
        already_sent = overwrite_index.already_sent(cells)
        if already_sent and overwrites is OverwritePosture.REFUSE:
            overwritten[entry.response_id] = already_sent
            refused.add(entry.response_id)
            _record_overwrite_refusal(entry, already_sent, refused_at=refused_at, moving=moving)
        else:
            try:
                imported = await post_translation(client, result, dry_run=dry_run)
            except (Dhis2ApiError, AuthenticationError, httpx2.HTTPError) as error:
                return _PostedPayloads(
                    imports=imports,
                    filed=filed,
                    overwritten=overwritten,
                    overwrite_refused=frozenset(refused),
                    filing_issues=tuple(issues),
                    stopped=_forward_stop(entry, error),
                )
            if imported.verdict is ForwardImportVerdict.NONE:
                return _PostedPayloads(
                    imports=imports,
                    filed=filed,
                    overwritten=overwritten,
                    overwrite_refused=frozenset(refused),
                    filing_issues=tuple(issues),
                    stopped=_non_verdict_stop(entry, imported),
                )
            if entry.held_for_retry and _only_says_it_exists(imported):
                # The identity is this receipt's own, so this is the earlier attempt landing rather than a refusal.
                imported = imported.model_copy(
                    update={"status": _ACCEPTED_IMPORT_STATUS, "issues": (), "message": _ALREADY_LANDED_MESSAGE}
                )
            imports[entry.response_id] = imported
            if cells and imported.is_accepted:
                if already_sent:
                    overwritten[entry.response_id] = already_sent
                overwrite_index.record(
                    cells, ForwardedSubmission(response_id=entry.response_id, received_at=entry.received_at)
                )
            if moving:
                _file_now(
                    entry,
                    imported,
                    result.target_kind,
                    cells,
                    filed,
                    issues,
                    completeness=_owed_claim_outcome(result, imported, registering=registering),
                )
        if posted % _POST_TICK_INTERVAL == 0 or posted == len(translated):
            progress.tick(_post_caption(posted, len(translated), dry_run=dry_run))
    return _PostedPayloads(
        imports=imports,
        filed=filed,
        overwritten=overwritten,
        overwrite_refused=frozenset(refused),
        filing_issues=tuple(issues),
    )


def _only_says_it_exists(imported: ForwardImportOutcome) -> bool:
    """Whether DHIS2's only objection to this payload is that it already holds what the payload creates."""
    return bool(imported.issues) and all(issue.error_code in _ALREADY_EXISTS_CODES for issue in imported.issues)


def _owed_claim_outcome(
    result: ConversionResult, imported: ForwardImportOutcome, *, registering: bool
) -> ForwardCompletenessOutcome | None:
    """The completeness a receipt is filed owing, which is what makes the claim survive the drain.

    A claim is owed the moment DHIS2 takes the values and until the registration is posted, so the
    sidecar records it as `pending` at filing time and the registration step overwrites it with the
    answer. A run that registers nothing owes nothing, and neither does a response that claimed none
    or a payload DHIS2 refused.
    """
    claim = result.completeness
    if claim is None or not registering or not imported.is_accepted:
        return None
    return _completeness_outcome(ForwardCompletenessKind.PENDING, claim)


def _file_now(
    entry: SpooledResponse,
    imported: ForwardImportOutcome,
    target_kind: ConversionTargetKind | None,
    cells: tuple[AggregateCell, ...],
    filed: dict[str, Path],
    issues: list[ForwardFilingIssue],
    *,
    completeness: ForwardCompletenessOutcome | None = None,
) -> None:
    """Move one receipt into the state DHIS2 just put it in, its import report written down first.

    Only an answer that reached a verdict gets here: `forwarded/` for the payload DHIS2's own import
    report says it took, `rejected/` for the one that report refuses. An answer carrying no import
    report at all stops the drain instead, with the receipt left in `received/`.

    A receipt whose file has already gone is graded rather than raised: DHIS2 has answered about the
    payload, and losing the rest of the drain over a rename that lost a race would throw that answer
    away along with every receipt still queued behind it.

    The report carries the cells an aggregate payload named and the day the receipt arrived, which is
    what lets the next drain say that a value it is about to send is one this receipt already sent.
    """
    record = ForwardImportRecord(
        **dict(imported),
        target_kind=target_kind,
        received_at=entry.received_at or None,
        cells=cells,
        completeness=completeness,
    )
    try:
        filed[entry.response_id] = (
            move_to_forwarded(entry, record) if imported.is_accepted else move_to_rejected(entry, record)
        )
    except FileNotFoundError as error:
        issues.append(
            ForwardFilingIssue(
                response_id=entry.response_id,
                reason=f"{entry.path} was gone when the drain went to file it ({error}); DHIS2's answer is in "
                f"this report and the receipt is wherever whatever moved it put it",
            )
        )


def _file_terminal_refusals(
    spooled: Sequence[SpooledResponse], conversion: ConversionReport, *, moving: bool
) -> _FiledReceipts:
    """File every receipt whose refusal nothing can fix, so no drain ever translates it again.

    `TERMINAL_REFUSAL_CATEGORIES` is the whole of the judgment, and the sidecar states it in the
    receipt's own directory so the file explains itself. A dry run files nothing, exactly as it moves
    nothing else, and `d2w fhir requeue` is the way back for an operator who disagrees.
    """
    if not moving:
        return _FiledReceipts()
    paths: dict[str, Path] = {}
    issues: list[ForwardFilingIssue] = []
    for entry, result in zip(spooled, conversion.results, strict=True):
        if not _is_terminally_refused(result):
            continue
        record = ForwardImportRecord(
            status=_TERMINAL_REFUSAL_STATUS,
            message=_TERMINAL_REFUSAL_MESSAGE,
            issues=tuple(
                ForwardImportIssue(error_code=refusal.category.value, subject=refusal.element, message=refusal.reason)
                for refusal in result.refusals
            ),
            target_kind=result.target_kind,
        )
        try:
            paths[entry.response_id] = move_to_rejected(entry, record)
        except FileNotFoundError as error:
            issues.append(
                ForwardFilingIssue(
                    response_id=entry.response_id,
                    reason=f"{entry.path} was gone when the drain went to file it ({error})",
                )
            )
    return _FiledReceipts(paths=paths, issues=tuple(issues))


def _is_terminally_refused(result: ConversionResult) -> bool:
    """Whether a refusal is one no change to the guide and no change to the data could ever resolve."""
    return any(refusal.category in TERMINAL_REFUSAL_CATEGORIES for refusal in result.refusals)


def _record_refusals(spooled: Sequence[SpooledResponse], conversion: ConversionReport, *, moving: bool) -> None:
    """Write the refusal record beside every receipt this committing drain refused and left queued.

    The receipt stays in `received/` for the next drain to retry, and until now it read in a
    listing exactly like one no drain had touched. The record beside it - the drain's instant, how
    many drains have refused it so far, and why - is what lets `/facade/spool` and `d2w fhir spool` say
    the difference. A dry run writes nothing, exactly as it moves nothing; a terminal refusal is
    filed to `rejected/` instead - see `_file_terminal_refusals`.
    """
    if not moving:
        return
    refused_at = _utc_instant()
    for entry, result in zip(spooled, conversion.results, strict=True):
        if not result.is_refused or _is_terminally_refused(result):
            continue
        previous = read_refusal_record(entry.path.parent, entry.response_id)
        record_refusal(
            entry,
            ForwardRefusalRecord(
                refused_at=refused_at,
                attempt_count=1 if previous is None else previous.attempt_count + 1,
                reasons=tuple(
                    RefusalReason(category=refusal.category.value, element=refusal.element, reason=refusal.reason)
                    for refusal in result.refusals
                ),
            ),
        )


def _record_overwrite_refusal(
    entry: SpooledResponse, values: Sequence[OverwrittenValue], *, refused_at: str, moving: bool
) -> None:
    """Write the refusal record beside one receipt this committing drain would not overwrite with.

    The same sidecar a translator refusal writes, under a category of its own, because the receipt
    ends in the same place: still in `received/`, still drainable, and now carrying the reason. Each
    value it was refused over is a reason of its own, naming the cell, the receipt that sent it, and
    when that receipt arrived - which is what an operator needs to decide between flipping the dial
    and going back to the earlier submission. The attempt count carries over from whatever record was
    already there, so a receipt three drains have refused says three whichever refusal it met.

    A dry run writes nothing, exactly as it moves nothing: it says what it would refuse and leaves
    the queue as it found it.
    """
    if not moving:
        return
    previous = read_refusal_record(entry.path.parent, entry.response_id)
    record_refusal(
        entry,
        ForwardRefusalRecord(
            refused_at=refused_at,
            attempt_count=1 if previous is None else previous.attempt_count + 1,
            reasons=tuple(
                RefusalReason(category=OVERWRITE_REFUSAL_CATEGORY, element=value.cell.data_element, reason=value.line)
                for value in values
            ),
        ),
    )


def _utc_instant() -> str:
    """The current instant as a FHIR `instant` - UTC, seconds precision, `Z`-suffixed."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _non_verdict_stop(entry: SpooledResponse, imported: ForwardImportOutcome) -> ForwardStop:
    """Name the answer that was about the run rather than about the payload, so the operator reads a status.

    Whatever answered was not the import endpoint reaching a verdict, so the receipt is left in
    `received/` exactly as a 5xx leaves it and every receipt behind it keeps its turn. The status is
    the whole of the lead: a 401 or a 403 is a credential, a 404 is a path or a proxy, a 429 is a
    rate limiter, and a success carrying no report is an endpoint answering something else entirely.
    """
    answered = f"answered {imported.http_status}" if imported.http_status is not None else "answered a success status"
    stated = f": {imported.message}" if imported.message else ""
    return ForwardStop(
        response_id=entry.response_id,
        status_code=imported.http_status,
        reason=f"the instance {answered} with no import report, which is no verdict about the payload{stated}",
    )


def _forward_stop(entry: SpooledResponse, error: Dhis2ApiError | AuthenticationError | httpx2.HTTPError) -> ForwardStop:
    """Name what stopped one drain, in the terms the operator has to act on."""
    if isinstance(error, AuthenticationError):
        return ForwardStop(
            response_id=entry.response_id,
            status_code=_UNAUTHORIZED_STATUS,
            reason=f"the instance answered {_UNAUTHORIZED_STATUS} and took no payload: {error}",
        )
    if isinstance(error, Dhis2ApiError):
        return ForwardStop(
            response_id=entry.response_id,
            status_code=error.status_code,
            reason=f"the instance answered {error.status_code} rather than an import report: {error.message}",
        )
    return ForwardStop(response_id=entry.response_id, reason=f"the instance could not be reached: {error}")


class _OwedCompletenessClaim(BaseModel):
    """One forwarded receipt whose values landed and whose completeness registration did not."""

    model_config = ConfigDict(frozen=True)

    response_id: str
    claim: CompleteDataSetRegistration


def _forwarded_import_record(layout: SpoolLayout, response_id: str) -> ForwardImportRecord | None:
    """One forwarded receipt's sidecar, or None when there is none to read or it will not parse."""
    for filed_id, text in read_import_reports(layout, SpoolState.FORWARDED):
        if filed_id != response_id:
            continue
        try:
            return ForwardImportRecord.model_validate_json(text)
        except (ValidationError, ValueError):
            return None
    return None


def _owed_completeness_claims(layout: SpoolLayout) -> tuple[_OwedCompletenessClaim, ...]:
    """Every completeness claim `forwarded/` records as still owed, in the order the directory reads.

    One pass over the sidecars, which is the same pass `build_forwarded_cell_index` takes and the
    price of a claim that survives the drain that made it. A sidecar that will not read is left out
    rather than raised: one unreadable file must not cost the other claims their retry.
    """
    owed: list[_OwedCompletenessClaim] = []
    for response_id, text in read_import_reports(layout, SpoolState.FORWARDED):
        try:
            record = ForwardImportRecord.model_validate_json(text)
        except (ValidationError, ValueError):
            continue
        outcome = record.completeness
        if outcome is None or outcome.kind is not ForwardCompletenessKind.PENDING:
            continue
        if not outcome.data_set or not outcome.period or not outcome.organisation_unit:
            continue
        owed.append(
            _OwedCompletenessClaim(
                response_id=response_id,
                claim=CompleteDataSetRegistration(
                    dataSet=outcome.data_set,
                    period=outcome.period,
                    organisationUnit=outcome.organisation_unit,
                    attributeOptionCombo=outcome.attribute_option_combo,
                    date=outcome.date,
                    completed=True,
                ),
            )
        )
    return tuple(owed)


async def _retry_owed_completeness(
    client: Dhis2Client,
    layout: SpoolLayout,
    owed: Sequence[_OwedCompletenessClaim],
    progress: StepAnnouncer,
) -> tuple[ForwardCompletenessRetry, ...]:
    """Post every registration an earlier drain left owed, without posting a single value again.

    The values are in DHIS2 already - what is missing is the second write that says the report is
    complete - so the claim is rebuilt from the tuple its own sidecar recorded and posted on its
    own. Registering a tuple DHIS2 already holds is an update rather than a conflict, so a claim
    that turns out to have landed after all costs nothing.
    """
    retries: list[ForwardCompletenessRetry] = []
    for posted, owing in enumerate(owed, start=1):
        progress.tick(f"registering reports an earlier drain left owed ({posted:,})")
        outcome = await _completeness_answer(client, owing.claim)
        _record_completeness_in_sidecar(layout, owing.response_id, outcome)
        retries.append(ForwardCompletenessRetry(response_id=owing.response_id, outcome=outcome))
    return tuple(retries)


async def _register_completeness(
    client: Dhis2Client,
    layout: SpoolLayout,
    spooled: Sequence[SpooledResponse],
    conversion: ConversionReport,
    imports: dict[str, ForwardImportOutcome],
    *,
    dry_run: bool,
    registering: bool,
    overwrite_refused: frozenset[str],
    progress: StepAnnouncer,
) -> dict[str, ForwardCompletenessOutcome]:
    """Register the data set complete for every `completed` aggregate response DHIS2 has just taken.

    The order is the whole safety property: this runs after `_post_translations`, and a response is
    only registered when its own import answer says DHIS2 took the values. A response the instance
    rejected registers nothing, because completeness is a claim about data that landed - and DHIS2
    itself is looser than that, registering off `/api/dataValueSets`' own `completeDate` even when
    every value in the envelope was refused and even under `dryRun=true` (DHIS2_ISSUES.md 78, 79), which is
    exactly why the forwarder never writes that field and states the claim here instead.

    A dry run posts nothing. The endpoint has a `dryRun` of its own, but a dry run wrote no values for
    the claim to be about, so the honest outcome is the tuple that would be registered.

    A refusal is recorded and does not change what the response became. The values are imported and
    stay imported; unwinding them over a failed second write would turn one refused claim into a lost
    report. Registering a tuple twice is an update rather than a conflict, so a claim that did not
    land can simply be posted again.

    A registration that never reaches the instance at all - a timeout, a dropped connection, an
    answer carrying no import summary - is caught per claim and recorded as `pending` rather than
    left to escape the run. The values are in DHIS2 either way, and an exception here would take the
    whole drain's report down over a second write it has already made durable: every filed receipt's
    sidecar records the claim as `pending` from the moment it is filed, and the answer is written
    over it here, so `_owed_completeness_claims` finds whatever this run did not finish.

    A response the run refused as an overwrite claims nothing at all, on a dry run as much as on an
    import: it was never sent, so there is no report for a completeness claim to be about.
    """
    claims = [
        (entry, result)
        for entry, result in zip(spooled, conversion.results, strict=True)
        if result.completeness is not None or result.payload_of(DataValueSet) is not None
    ]
    outcomes: dict[str, ForwardCompletenessOutcome] = {}
    posted = 0
    for entry, result in claims:
        if entry.response_id in overwrite_refused:
            continue
        claim = result.completeness
        if claim is None:
            outcomes[entry.response_id] = ForwardCompletenessOutcome(kind=ForwardCompletenessKind.NOT_CLAIMED)
            continue
        if not registering:
            outcomes[entry.response_id] = _completeness_outcome(ForwardCompletenessKind.NOT_REGISTERED, claim)
            continue
        if dry_run:
            outcomes[entry.response_id] = _completeness_outcome(ForwardCompletenessKind.WOULD_REGISTER, claim)
            continue
        imported = imports.get(entry.response_id)
        if imported is None or not imported.is_accepted:
            continue
        posted += 1
        progress.tick(f"registering completed reports ({posted:,})")
        outcome = await _completeness_answer(client, claim)
        outcomes[entry.response_id] = outcome
        _record_completeness_in_sidecar(layout, entry.response_id, outcome)
    return outcomes


async def _completeness_answer(client: ImportPoster, claim: CompleteDataSetRegistration) -> ForwardCompletenessOutcome:
    """Post one registration and answer with what became of the claim, an unreachable instance included.

    A registration is a second write about values that are already in DHIS2, so nothing it meets is
    worth losing the run over. What the instance never answered is a claim still owed, which is what
    `pending` says and what the next drain acts on.
    """
    try:
        return await _post_completeness(client, claim)
    except (Dhis2ApiError, AuthenticationError, httpx2.HTTPError) as error:
        return _completeness_outcome(
            ForwardCompletenessKind.PENDING,
            claim,
            message=f"the registration did not reach the instance ({error}), so the report is not registered",
        )


def _record_completeness_in_sidecar(layout: SpoolLayout, response_id: str, outcome: ForwardCompletenessOutcome) -> None:
    """Write what became of one claim into the forwarded receipt's own import report.

    The sidecar is the only durable place the claim can live once the receipt has left the queue,
    and a receipt whose file has moved on is graded rather than raised: DHIS2 has answered, and
    losing the run over a sidecar nobody can find would throw that answer away.
    """
    record = _forwarded_import_record(layout, response_id)
    if record is None:
        return
    write_import_report(layout, SpoolState.FORWARDED, response_id, record.model_copy(update={"completeness": outcome}))


async def _post_completeness(client: ImportPoster, claim: CompleteDataSetRegistration) -> ForwardCompletenessOutcome:
    """POST one completeness registration and project DHIS2's answer onto the outcome the report reads.

    The answer is `/api/dataValueSets`' own envelope - a `WebMessage` wrapping an `ImportSummary` - so
    the aggregate projection reads it unchanged. A registration DHIS2 has already stored counts
    `updated` rather than conflicting, which is what makes forwarding the same tuple twice safe.

    The three verdicts are the same three a value import gets. Only an answer carrying the endpoint's
    own import summary registers the tuple; a summary that refuses it is a refusal; and anything else
    - a gateway's `403`, a proxy's `404`, a rate limiter's `429`, a success carrying some other
    document - registered nothing, so the claim stays pending and the next drain posts it again.
    """
    body = CompleteDataSetRegistrations(completeDataSetRegistrations=[claim]).model_dump(
        by_alias=True, exclude_none=True, mode="json"
    )
    answer = _aggregate_import_outcome(await _post_body(client, _COMPLETE_DATA_SET_REGISTRATIONS_PATH, body, {}))
    if answer.verdict is ForwardImportVerdict.NONE:
        return _completeness_outcome(ForwardCompletenessKind.PENDING, claim, message=_no_verdict_message(answer))
    if answer.is_rejected:
        return _completeness_outcome(
            ForwardCompletenessKind.REFUSED, claim, message=answer.message, issues=answer.issues
        )
    return _completeness_outcome(ForwardCompletenessKind.REGISTERED, claim)


def _no_verdict_message(answer: ForwardImportOutcome) -> str:
    """Why one answer registered nothing, when what answered was not the endpoint reaching a verdict."""
    answered = f"answered {answer.http_status}" if answer.http_status is not None else "answered a success status"
    stated = f": {answer.message}" if answer.message else ""
    return f"the instance {answered} with no import summary, so the registration is not known to have landed{stated}"


def _completeness_outcome(
    kind: ForwardCompletenessKind,
    claim: CompleteDataSetRegistration,
    *,
    message: str | None = None,
    issues: tuple[ForwardImportIssue, ...] = (),
) -> ForwardCompletenessOutcome:
    """One claim as the outcome the report reads, carrying its four keys however the claim ended."""
    return ForwardCompletenessOutcome(
        kind=kind,
        data_set=claim.dataSet,
        period=claim.period,
        organisation_unit=claim.organisationUnit,
        attribute_option_combo=claim.attributeOptionCombo,
        date=claim.date,
        message=message,
        issues=issues,
    )


def _completeness_completion(
    completeness: dict[str, ForwardCompletenessOutcome], *, dry_run: bool, registering: bool
) -> str:
    """What the completeness step announces when it finishes, in the terms of the mode that ran."""
    if not completeness:
        return "no aggregate response to register"
    if not registering:
        return f"{len(completeness):,} aggregate response(s), completeness registration off"
    counted = Counter(outcome.kind for outcome in completeness.values())
    claimed = len(completeness) - counted[ForwardCompletenessKind.NOT_CLAIMED]
    if dry_run:
        return f"{claimed:,} report(s) would be registered complete (validate only)"
    return (
        f"{counted[ForwardCompletenessKind.REGISTERED]:,} report(s) registered complete, "
        f"{counted[ForwardCompletenessKind.REFUSED]:,} refused"
    )


def _completeness_retry_completion(retries: Sequence[ForwardCompletenessRetry]) -> str:
    """What a run with nothing to drain announces about the claims it found owed and posted again."""
    counted = Counter(retry.outcome.kind for retry in retries)
    registered = counted[ForwardCompletenessKind.REGISTERED]
    return f"{registered:,} of {len(retries):,} owed report(s) registered complete"


def _completeness_retry_note(retries: Sequence[ForwardCompletenessRetry]) -> str:
    """What a drain adds to its completeness line about the claims an earlier drain left owed."""
    return f", {_completeness_retry_completion(retries)}" if retries else ""


def _post_order(result: ConversionResult) -> int:
    """Where one translated payload sits in the posting order its target kind gives it."""
    if result.target_kind is None or result.target_kind not in FORWARD_TARGET_ORDER:
        return len(FORWARD_TARGET_ORDER)
    return FORWARD_TARGET_ORDER.index(result.target_kind)


async def post_translation(client: ImportPoster, result: ConversionResult, *, dry_run: bool) -> ForwardImportOutcome:
    """Post one translated payload to the endpoint its target kind names, and project DHIS2's answer."""
    payload = result.payload
    if payload is None or result.target_kind is None:
        # `ConversionResult.payload` answers None for a refused response alone, and a refused response
        # never reaches the posting loop - so this is the case the type system cannot exclude.
        raise ValueError("a translated result carries no payload at all")
    body = payload.model_dump(by_alias=True, exclude_none=True, mode="json")
    if result.target_kind is ConversionTargetKind.DATA_VALUE_SET:
        aggregate_params = dict(_DATA_VALUE_SETS_DRY_RUN_PARAMS) if dry_run else {}
        return _aggregate_import_outcome(await _post_body(client, _DATA_VALUE_SETS_PATH, body, aggregate_params))
    tracker_params = {**_TRACKER_PARAMS, **(_TRACKER_DRY_RUN_PARAMS if dry_run else {})}
    bundle = {_TRACKER_KEYS_BY_TARGET_KIND[result.target_kind]: [body]}
    return _tracker_import_outcome(await _post_body(client, _TRACKER_PATH, bundle, tracker_params))


async def _post_body(
    client: ImportPoster,
    path: str,
    body: dict[str, Any],
    params: dict[str, str],
) -> ForwardEndpointAnswer:
    """POST one payload and answer with DHIS2's own JSON body under the status it arrived with.

    A refused import is `409 Conflict` carrying the endpoint's report, so a rejection is an outcome to
    record rather than an error to raise. The body is passed on **raw** rather than parsed here, because
    the two endpoints do not agree on whether the report is wrapped: `/api/dataValueSets` answers a
    `WebMessage` whose `response` is the `ImportSummary`, and `/api/tracker` answers the
    `TrackerImportReport` bare, with no envelope around it at all - so the one shape that can carry both
    is the body itself, and each family unwraps its own. Anything the error carries no JSON object for -
    an authentication failure, an unreachable instance - is about the run and not about one response,
    and is raised.

    A 5xx is raised whatever it carries. DHIS2 and the proxies in front of it answer a server error with
    a `WebMessage` of their own, `status` and all, and reading that as the endpoint's verdict would
    project `status=ERROR` into a rejection and file the receipt under a refusal DHIS2 never made. The
    status is what separates the two: 409 is the endpoint reaching a verdict, 500 is it failing to.

    The status rides along with the body because a status below 500 does not make an answer a
    verdict either: a 401, a 403, a 404 and a 429 all carry JSON of their own from whatever answered
    instead of the endpoint, and the projection that reads the body says so in the status it carries.
    """
    try:
        return ForwardEndpointAnswer(body=await client.post_raw(path, body, params=params))
    except Dhis2ApiError as error:
        if error.status_code >= _SERVER_ERROR_STATUS:
            raise
        if isinstance(error.body, dict):
            return ForwardEndpointAnswer(status_code=error.status_code, body=error.body)
        raise


def _aggregate_import_outcome(answer: ForwardEndpointAnswer) -> ForwardImportOutcome:
    """Project an `/api/dataValueSets` answer: the import counts, every conflict it named, and its status."""
    body = answer.body
    envelope = _envelope(body)
    summary = _report_model(ImportSummary, _report_body(body, _DATA_VALUE_SET_REPORT_KEYS))
    counts = summary.importCount if summary is not None else None
    status = summary.status.value if summary is not None and summary.status is not None else envelope.get("status")
    conflicts = summary.conflicts if summary is not None else None
    return ForwardImportOutcome(
        status=_text(status),
        message=(summary.description if summary is not None else None) or _text(envelope.get("message")),
        created=counts.imported or 0 if counts is not None else 0,
        updated=counts.updated or 0 if counts is not None else 0,
        ignored=counts.ignored or 0 if counts is not None else 0,
        deleted=counts.deleted or 0 if counts is not None else 0,
        issues=tuple(_conflict_issue(conflict) for conflict in conflicts or []),
        data_value_summary=summary,
        http_status=answer.status_code,
        report_recognised=_carries_report(body, _DATA_VALUE_SET_REPORT_KEYS),
    )


def _tracker_import_outcome(answer: ForwardEndpointAnswer) -> ForwardImportOutcome:
    """Project an `/api/tracker` answer: the stats, every validation error it reported, and its status."""
    body = answer.body
    envelope = _envelope(body)
    report = _report_model(TrackerImportReport, _report_body(body, _TRACKER_REPORT_KEYS))
    stats = report.stats if report is not None else None
    status = report.status.value if report is not None and report.status is not None else envelope.get("status")
    validation = report.validationReport if report is not None else None
    errors = validation.errorReports if validation is not None else None
    return ForwardImportOutcome(
        status=_text(status),
        message=(report.message if report is not None else None) or _text(envelope.get("message")),
        created=stats.created or 0 if stats is not None else 0,
        updated=stats.updated or 0 if stats is not None else 0,
        ignored=stats.ignored or 0 if stats is not None else 0,
        deleted=stats.deleted or 0 if stats is not None else 0,
        issues=tuple(_tracker_issue(error) for error in errors or []),
        tracker_report=report,
        http_status=answer.status_code,
        report_recognised=_carries_report(body, _TRACKER_REPORT_KEYS),
    )


def _envelope(body: dict[str, Any]) -> dict[str, Any]:
    """The `WebMessage` fields of an answer, which are the body's own when no envelope wrapped the report."""
    return body


def _carries_report(body: dict[str, Any], keys: frozenset[str]) -> bool:
    """Whether one answer carries the import report of the endpoint it came from, wrapped or bare.

    The same reading `_report_body` takes, as the claim on its own: the fields only that endpoint's
    report has are in the body, or in the `WebMessage.response` an envelope wrapped it in. A body
    that has neither is some other party's answer, whatever HTTP status it arrived under.
    """
    nested = body.get("response")
    return bool(keys & body.keys()) or (isinstance(nested, dict) and bool(keys & nested.keys()))


def _report_body(body: dict[str, Any], keys: frozenset[str]) -> dict[str, Any] | None:
    """The endpoint's own report, wherever it arrived: inside a `WebMessage.response`, or bare as the body.

    `keys` are the fields only that endpoint's report carries, so the choice is made on what the document
    holds rather than on which HTTP status brought it - the same 409 body arrives wrapped from one
    endpoint and bare from the other.
    """
    nested = body.get("response")
    if isinstance(nested, dict) and keys & nested.keys():
        return nested
    if keys & body.keys():
        return body
    return nested if isinstance(nested, dict) else None


def _report_model[T: BaseModel](model: type[T], report_body: dict[str, Any] | None) -> T | None:
    """Validate one endpoint's report against its generated schema, keeping None when there is nothing to read.

    A report the generated model cannot read costs its own detail and nothing else: the run still records
    the rejection, and the endpoint said something this client's schema does not describe, which is a note
    for `DHIS2_ISSUES.md` rather than a reason to lose the other two hundred outcomes.
    """
    if not report_body:
        return None
    try:
        return model.model_validate(report_body)
    except ValidationError:
        return None


def _text(value: object) -> str | None:
    """One wire field as the string a report carries it as, or None when DHIS2 sent nothing."""
    return str(value) if value is not None else None


def _conflict_issue(conflict: ImportConflict) -> ForwardImportIssue:
    """One `/api/dataValueSets` conflict as the row both report shapes fold into."""
    return ForwardImportIssue(
        error_code=conflict.errorCode,
        subject=conflict.object or conflict.property,
        message=conflict.value,
    )


def _tracker_issue(error: TrackerImportError) -> ForwardImportIssue:
    """One `/api/tracker` validation error as the row both report shapes fold into."""
    return ForwardImportIssue(error_code=error.errorCode, subject=error.uid, message=error.message)


def _generalised_reason(reason: str, rule_names: ProgramRuleNames, *, elide: bool = True) -> str:
    """One DHIS2 message read back for a person: a published rule by name, every other UID generalised away.

    DHIS2 names the program rule that refused an import by UID alone (`E1300`), and the guide
    published that UID beside the rule's own name, so the roll-up says which rule refused rather
    than which twelve characters did. Every other identifier generalises, because two rejections of
    one rule against two different objects are one cause of the run.

    QUOTED AND BARE ALIKE, AND BY THE SAME TEST. Whether DHIS2 backticks an identifier is DHIS2's own
    habit, not a fact about the identifier - `E8025` states the attribute option combo bare and the
    organisation units it is not usable with quoted, in one sentence - so a row grouping three
    responses would otherwise name the first one's combo for all three. What DHIS2 quotes is not
    thereby an identifier either: `E1302` backticks a value type, a whole explanatory clause, and the
    offending value inside it, so a quoted run is read for the same shape a bare one is and stays
    verbatim when it is prose, a value, or a name. The UID itself is untouched on the response's own
    report, which is where a reader goes for the object.

    `elide = False` is the row standing for a single response: the rule name still lands, because
    reading `dahuKlP7jR2` back as the rule it published is a gain at any count, and every other
    identifier stays as DHIS2 wrote it, because there is no second object for it to be wrong about.
    """

    def _read(match: re.Match[str]) -> str:
        token = match.group(0)
        quoted = token.startswith("`")
        inner = token.strip("`")
        name = rule_names.name_for(inner)
        if name is not None:
            return f"`{name}`"
        identifies = _quotes_identifiers(inner) if quoted else _reads_as_uid(token)
        if not identifies:
            return token
        return "`...`" if elide else token

    return _EMBEDDED_IDENTIFIER.sub(_read, reason)


def _quotes_identifiers(run: str) -> bool:
    """Whether one backticked run holds DHIS2 identifiers and nothing else.

    DHIS2 quotes a list of organisation units and it quotes `Value type is NUMBER but the value` in
    the same habit, so the run earns generalisation only when every token in it is UID-shaped and
    reads as a UID rather than as a word. An empty run holds no identifier and says nothing.
    """
    tokens = [token for token in _QUOTED_TOKEN_SEPARATOR.split(run.strip()) if token]
    return bool(tokens) and all(_UID_SHAPE.fullmatch(token) is not None and _reads_as_uid(token) for token in tokens)


def _reads_as_uid(token: str) -> bool:
    """Whether a bare eleven-character word is a DHIS2 UID rather than a word of the sentence around it.

    A UID carries a digit, or turns from lower case to upper more often than a word does. DHIS2 writes
    `DataElement`, `dataElement` and `Enrollments` into the very messages this reads, each of them
    eleven characters and each of them prose, so shape is what separates them from `ImspTQPwCqd`.
    """
    return any(character.isdigit() for character in token) or len(_CASE_TURN.findall(token)) > _CASE_TURNS_OF_A_WORD


def _rejection_cause_key(error_code: str | None, generalised_reason: str) -> tuple[str | None, str]:
    """What a rejection rolls up under: the error code alone, or the generalised message when there is no code.

    An error code names a DHIS2 rule identically on every major, while the wording around it drifts, so
    a coded row carries no message in its key and a codeless one has nothing else to be named by.
    """
    return (error_code, "") if error_code else (None, generalised_reason)


def _collect_outcomes(
    spooled: Sequence[SpooledResponse],
    conversion: ConversionReport,
    imports: dict[str, ForwardImportOutcome],
    completeness: dict[str, ForwardCompletenessOutcome],
    project_root: Path,
    *,
    filed: dict[str, Path],
    minted_enrollments: frozenset[str],
    overwritten: dict[str, tuple[OverwrittenValue, ...]],
    overwrite_refused: frozenset[str],
) -> tuple[ForwardOutcome, ...]:
    """Pair every receipt with what DHIS2 said about it, and with where the run left its file.

    Nothing moves here. The renames happened as the verdicts arrived, so this states the spool as it
    now is: a receipt the run filed is named at its new path, and one nothing was decided about -
    refused, unverifiable, or never reached - is named where the run found it.

    `minted_enrollments` is empty on an import run, which is what keeps the unverifiable reading a
    dry-run reading: an import creates the enrollments it posts, so nothing it rejects goes unchecked.
    """
    outcomes: list[ForwardOutcome] = []
    for entry, result in zip(spooled, conversion.results, strict=True):
        imported = imports.get(entry.response_id)
        refused_over_an_overwrite = entry.response_id in overwrite_refused
        kind = _outcome_kind(result, imported, minted_enrollments, refused_over_an_overwrite)
        path = filed.get(entry.response_id, entry.path)
        outcomes.append(
            ForwardOutcome(
                response_id=entry.response_id,
                questionnaire=result.questionnaire or entry.questionnaire or None,
                target_kind=result.target_kind,
                kind=kind,
                notes=result.notes,
                refusals=result.refusals,
                import_outcome=imported,
                completeness=completeness.get(entry.response_id),
                overwritten_values=overwritten.get(entry.response_id, ()),
                overwrite_refused=refused_over_an_overwrite,
                spool_path=_relative_path(path, project_root),
                submitted_by=entry.submitted_by,
            )
        )
    return tuple(outcomes)


def _minted_enrollment_uids(conversion: ConversionReport) -> frozenset[str]:
    """Every enrollment UID this run's registrations mint, which is what their stage events name.

    The UIDs are the client's own - a registration response carries the enrollment it creates in its
    `D2TrackerEnrollment` extension - so they are known before DHIS2 answers anything. Both
    registration shapes mint one: the person the run creates carries theirs inside them, and the
    person the instance already holds is enrolled by a payload that is the enrollment itself.
    """
    nested = (
        enrollment for tracked_entity in conversion.tracked_entities for enrollment in tracked_entity.enrollments or []
    )
    return frozenset(
        enrollment.enrollment for enrollment in (*nested, *conversion.enrollments) if enrollment.enrollment
    )


def _outcome_kind(
    result: ConversionResult,
    imported: ForwardImportOutcome | None,
    minted_enrollments: frozenset[str],
    overwrite_refused: bool,
) -> ForwardOutcomeKind:
    """Which of the five states one receipt ended in.

    A translated receipt with no import answer is one the drain stopped short of, not one the
    translator refused: the two are different facts about different failures, and calling the first
    the second would report a healthy receipt as unreadable and hide that the run ended early. A
    receipt the run would not overwrite with is the same state as a translator refusal - it stayed
    in the queue and DHIS2 was never asked - so it is graded the same, and the outcome says which
    refusal it met.
    """
    if result.is_refused or overwrite_refused:
        return ForwardOutcomeKind.REFUSED
    if imported is None:
        return ForwardOutcomeKind.NOT_POSTED
    if not imported.is_rejected:
        return ForwardOutcomeKind.ACCEPTED
    if _is_unverifiable(result, imported, minted_enrollments):
        return ForwardOutcomeKind.UNVERIFIABLE
    return ForwardOutcomeKind.REJECTED


def _is_unverifiable(
    result: ConversionResult,
    imported: ForwardImportOutcome,
    minted_enrollments: frozenset[str],
) -> bool:
    """Whether a rejection is only DHIS2 saying the enrollment this event names does not exist yet.

    Three things have to hold together. The payload is a tracker event; every row DHIS2 named against
    it is one of the pair it answers an absent enrollment with (DHIS2_ISSUES.md 68); and the enrollment the
    event names is one a registration of the same run mints. An event naming an enrollment nobody in
    the run creates fails the last test and stays a rejection, which is the orphan the run must state.
    """
    event = result.payload_of(TrackerEvent)
    if event is None or event.enrollment not in minted_enrollments:
        return False
    error_codes = {issue.error_code for issue in imported.issues}
    return bool(error_codes) and all(code in _ABSENT_ENROLLMENT_ERROR_CODES for code in error_codes)


def _relative_path(path: Path, project_root: Path) -> str:
    """Name one spool file relative to the project when it lives inside it, so the report stays portable."""
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


async def drain_responses(
    client: Dhis2Client,
    project: FhirProject,
    artifacts: CompiledArtifacts,
    *,
    import_responses: bool | None = None,
    coded_answer_mode: CodedAnswerMode | None = None,
    register_completeness: bool | None = None,
    overwrites: OverwritePosture | None = None,
    corrections: CorrectionPosture | None = None,
    withdrawals: WithdrawalPosture | None = None,
    reporter: ProgressReporter | None = None,
    only_held_for_retry: bool = False,
) -> ForwardReport:
    """Drain a project's capture spool through a connection the caller holds, against a guide it already read.

    A dry run is the default, under each endpoint's own validate-only mode. Every dial left as None falls
    to what the project's configuration says. The connection stays open afterwards: its lifetime is the
    caller's. The lock, the filing, and the stop on a server error are the same ones `d2w fhir forward` runs.
    `only_held_for_retry` sends what a server took on sending, and leaves the rest of the queue where it is.
    """

    @asynccontextmanager
    async def connect() -> AsyncGenerator[Dhis2Client]:
        """Hand over the caller's connection and leave it open."""
        yield client

    forward_config = project.config.forward
    with drain_lock(spool_layout(project)):
        return await drain_spool(
            project,
            connect=connect,
            read_guide=lambda: artifacts,
            build_guide=None,
            mode=coded_answer_mode if coded_answer_mode is not None else configured_coded_answer_mode(project),
            import_responses=import_responses if import_responses is not None else forward_config.import_responses,
            register_completeness=(
                register_completeness if register_completeness is not None else forward_config.register_completeness
            ),
            overwrites=overwrites if overwrites is not None else forward_config.overwrites,
            corrections=corrections if corrections is not None else forward_config.corrections,
            withdrawals=withdrawals if withdrawals is not None else forward_config.withdrawals,
            reporter=reporter,
            only_held_for_retry=only_held_for_retry,
        )


async def claim_completeness(client: ImportPoster, claim: CompleteDataSetRegistration) -> ForwardCompletenessOutcome:
    """Register one report complete, once its values landed. A claim that did not reach DHIS2 is left pending.

    The values are already in DHIS2 when this runs, so a failure here is never a reason to refuse them.
    """
    try:
        return await _completeness_answer(client, claim)
    except (Dhis2ClientError, ServeError) as error:
        return _completeness_outcome(ForwardCompletenessKind.PENDING, claim, message=str(error))
