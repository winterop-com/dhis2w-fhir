"""The records a drain leaves behind: what DHIS2 answered, in the shape the spool files beside a receipt."""

from __future__ import annotations

from enum import StrEnum

from dhis2w_client.generated.v43.oas import ImportSummary, TrackerImportReport
from pydantic import BaseModel, ConfigDict

from dhis2w_fhir.conversion.schemas import ConversionTargetKind
from dhis2w_fhir.overwrite import AggregateCell


class ForwardCompletenessKind(StrEnum):
    """What became of one aggregate response's completeness claim."""

    #: DHIS2 took the registration: the data set is complete for the tuple the values landed under.
    REGISTERED = "registered"

    #: A dry run states the tuple a `completed` response would register, and posts nothing.
    WOULD_REGISTER = "would-register"

    #: The response reports itself `in-progress`, so its values imported and it claims nothing.
    NOT_CLAIMED = "not-claimed"

    #: The values imported and DHIS2 refused the registration; the values stay imported.
    REFUSED = "refused"

    #: The run was told not to register completeness, so a `completed` response claims and posts nothing.
    NOT_REGISTERED = "not-registered"

    #: The values landed and the registration did not: the claim is owed, and the next drain posts it.
    PENDING = "pending"


class ForwardCompletenessRetry(BaseModel):
    """One completeness claim an earlier drain left owed, and what this drain's registration answered.

    A retry is about a receipt that is already in `forwarded/`, so it is no part of this run's
    spool: the values it claims completeness for landed in an earlier drain, and only the
    registration is outstanding.
    """

    model_config = ConfigDict(frozen=True)

    response_id: str
    outcome: ForwardCompletenessOutcome


class ForwardImportVerdict(StrEnum):
    """What one HTTP answer from an import endpoint says about the payload that was posted to it."""

    #: The endpoint's own import report arrived and names no refusal: DHIS2 took the payload.
    ACCEPTED = "accepted"

    #: The endpoint's own import report arrived and refuses the payload, by status or by named rows.
    REJECTED = "rejected"

    #: Nothing that reads as the endpoint's import report arrived, so the answer is about the run.
    NONE = "none"


class ForwardImportIssue(BaseModel):
    """One row DHIS2 named as a reason it would not take the payload, from either report shape.

    `/api/dataValueSets` names them `response.conflicts[]` (`errorCode`, `object`, `value`) and
    `/api/tracker` names them `validationReport.errorReports[]` (`errorCode`, `uid`, `message`). One
    shape, because a reader of either wants the same three things: which rule, which object, what it said.
    """

    model_config = ConfigDict(frozen=True)

    error_code: str | None = None
    subject: str | None = None
    """The object the row is about - the DHIS2 UID a tracker error names, or the conflicting object."""

    message: str | None = None

    @property
    def line(self) -> str:
        """The row as one readable line, which is what a report file and a terminal cell both want."""
        parts = [self.error_code, self.subject, self.message]
        return " ".join(part for part in parts if part) or "no reason given"

    @property
    def reason(self) -> str:
        """What the row says, falling back to the code when DHIS2 gave no message at all."""
        return self.message or self.error_code or "no reason given"


class ForwardCompletenessOutcome(BaseModel):
    """The tuple one aggregate response claims complete, and what DHIS2 said about the claim.

    The four keys are named here rather than left to the import summary because a completeness
    registration is DHIS2's one write with no identity of its own: there is no UID to look it up by,
    only the tuple, so a reader who wants to check it needs the tuple written down.
    """

    model_config = ConfigDict(frozen=True)

    kind: ForwardCompletenessKind
    data_set: str | None = None
    period: str | None = None
    organisation_unit: str | None = None
    attribute_option_combo: str | None = None
    """Unset where the data set rides the default category combo, which is what DHIS2 files it under."""

    date: str | None = None
    message: str | None = None
    """What DHIS2 said when it refused the registration, which is the only thing a refusal can be acted on."""

    issues: tuple[ForwardImportIssue, ...] = ()

    @property
    def tuple_line(self) -> str:
        """The four keys as the one cell a report shows, since the registration has no other name."""
        parts = [self.data_set, self.period, self.organisation_unit, self.attribute_option_combo]
        return " / ".join(part for part in parts if part) or "no tuple"

    @property
    def reason(self) -> str:
        """Why a refused registration was refused, as the one line a table cell and a report both want."""
        lines = [issue.line for issue in self.issues]
        return "; ".join(lines) if lines else (self.message or "DHIS2 gave no reason")


class ForwardImportOutcome(BaseModel):
    """One DHIS2 import answer, projected out of whichever of the two endpoint report shapes carried it.

    `/api/dataValueSets` answers with an `ImportSummary` - an `importCount` and a flat `conflicts` list -
    and `/api/tracker` answers with a `TrackerImportReport` - `stats` and a `validationReport`. The two
    have no shape in common, so this is what both fold into for counting and rendering, with the
    endpoint's own generated report riding alongside untouched for anyone who needs the detail.
    """

    model_config = ConfigDict(frozen=True)

    status: str | None = None
    message: str | None = None
    created: int = 0
    updated: int = 0
    ignored: int = 0
    deleted: int = 0
    issues: tuple[ForwardImportIssue, ...] = ()
    """Every row DHIS2 named as a reason, in the order the report listed them."""

    data_value_summary: ImportSummary | None = None
    tracker_report: TrackerImportReport | None = None

    http_status: int | None = None
    """The failing HTTP status the answer arrived under; None for a 2xx and for a locally made outcome."""

    report_recognised: bool = False
    """Whether the answer carried the import report of the endpoint it was posted to.

    A verdict is a report, not a status line. An authentication gateway, a reverse proxy, and a
    rate limiter all answer JSON of their own, and `{"message": "Forbidden"}` says nothing about
    an import - so what makes an answer a verdict is the endpoint's own report shape being in it.
    """

    @property
    def verdict(self) -> ForwardImportVerdict:
        """Whether this answer accepted the payload, refused it, or reached no verdict about it at all."""
        if not self.report_recognised:
            return ForwardImportVerdict.NONE
        return ForwardImportVerdict.REJECTED if self.is_rejected else ForwardImportVerdict.ACCEPTED

    @property
    def is_accepted(self) -> bool:
        """Whether DHIS2 took the payload: its own import report arrived, and that report names no refusal."""
        return self.verdict is ForwardImportVerdict.ACCEPTED

    @property
    def is_rejected(self) -> bool:
        """Whether DHIS2 refused the payload: an error status, or any row it named against the payload."""
        return self.status == _ERROR_IMPORT_STATUS or bool(self.issues)

    @property
    def counts_line(self) -> str:
        """What the import did, as the one cell a per-response table shows when there is no reason to show."""
        return f"{self.created} created, {self.updated} updated, {self.ignored} ignored"


class ForwardImportRecord(ForwardImportOutcome):
    """The sidecar beside a drained receipt: DHIS2's own answer, plus which payload was posted.

    One shape for both drained states. `rejected/<id>.report.json` says why DHIS2 refused the payload;
    `forwarded/<id>.report.json` says what it did with the one it took, which is the import counts -
    a receipt filed with nothing beside it makes "how much of this landed" a question the spool cannot
    answer, and the number is the same one an operator chases when a report comes out short.

    The target kind is what tells an operator reading either file cold which of the tracker shapes
    DHIS2 was given - a person and their enrollment, or the enrollment alone for a person the instance
    already held - without opening the receipt beside it and reading its extensions back.

    An aggregate payload also records the cells it landed on and the day its receipt arrived, which is
    what makes `forwarded/` answerable about a value a later drain sends again. Identity only, never
    the numbers: what a payload landed on is the spool's business and what it landed is the receipt's.
    """

    target_kind: ConversionTargetKind | None = None
    received_at: str | None = None
    """When the receipt this payload came off was captured, so a later drain can say when it was sent."""

    cells: tuple[AggregateCell, ...] = ()
    """Every aggregate value this payload named, by identity; empty for every tracker payload."""

    completeness: ForwardCompletenessOutcome | None = None
    """What became of this response's completeness claim, and None where it made none.

    Written twice on the way through: `pending` as the receipt is filed, and then the answer once
    the registration has been posted. The pending write is what makes the claim survive a drain that
    dies between the two - the values are in DHIS2 and the registration is not, and this sidecar is
    the only place that fact can live once the receipt has left the queue.
    """


class ForwardRejectionReason(BaseModel):
    """One cause a run's rejections roll up into, and how many responses met it.

    DHIS2 states a rule once and then names the objects that broke it, so two hundred rejections are
    usually a handful of causes. Grouping is on the error code, which is the stable name of a rule -
    the wording DHIS2 wraps it in differs between majors, so grouping on the message would split one
    rule into a row per version. `reason` is the first message the group met, with its quoted UIDs
    generalised away, kept as the sample a reader acts on. A row DHIS2 gave no code for groups on
    that generalised message instead, since it is the only name the rule has.
    """

    model_config = ConfigDict(frozen=True)

    error_code: str | None = None
    reason: str
    responses: int


#: What the sidecar of a withdrawn receipt says in the words a person reading `withdrawn/` cold
#: needs, which are not the words "deleted". DHIS2 soft-deletes: the row stays, carrying its value,
#: and it is gone from every ordinary read.
_WITHDRAWAL_MESSAGE = (
    "Withdrawn. This DHIS2 instance keeps a hidden copy of the event; it no longer appears in reports. "
    "The UID is burned, so this receipt can never be forwarded again."
)


class WithdrawalRecord(ForwardImportOutcome):
    """The sidecar beside a withdrawn receipt: what DHIS2 answered when it was asked to take the event back.

    A document of its own rather than a second import report, because it answers a different
    question. `forwarded/<id>.report.json` says what DHIS2 did with the payload when it took it and
    stays where it is; this one says what it did when it was asked to let go of it.
    """

    event_uid: str
    """The DHIS2 event this withdrawal named, derived from the receipt's own logical id."""

    withdrawn_at: str
    """The instant the withdrawal was posted, as a FHIR `instant` (UTC)."""

    received_at: str | None = None
    """When the receipt this withdrawal retracts was captured."""

    note: str = _WITHDRAWAL_MESSAGE
    """What remains in the instance, in the words a person reading this file cold needs them in."""

    @property
    def line(self) -> str:
        """The record as the one line a listing row shows: the fact, and the object it is about."""
        return f"withdrawn at {self.withdrawn_at}; event {self.event_uid} no longer appears in reports"


#: The status both endpoint report shapes spell an outright refusal as.
_ERROR_IMPORT_STATUS = "ERROR"
