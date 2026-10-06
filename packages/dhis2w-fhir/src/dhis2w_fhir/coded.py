"""The DHIS2 code and name one projection carries, and what a substitute-posture run publishes instead.

An R4 `code` admits single internal spaces, so `Pre eclampsia` reaches a guide as a legal concept
code. It is still a liability everywhere downstream: the IG publisher's anchor slug strips
whitespace, so `Pre eclampsia` and `Preeclampsia` render one anchor id (IG_PUBLISHER_ISSUES.md #107); a URL has to
escape it; CQL has to quote it; a terminology server round-trips it at its own discretion. A
production deployment would clean the code in DHIS2, so the substitute posture publishes it clean
without touching DHIS2: every space becomes a hyphen.

The projection keeps both spellings, of the code and of the name alike. `code` and `name` are what
the guide publishes; `original_code`, `original_name`, and `original_form_name` are what DHIS2
holds; `dhis2_code`, `dhis2_name`, and `dhis2_form_name` are the ones a caller joins back to DHIS2
on - so a rewrite never reaches a DHIS2 write, an import payload, or the `dhis2-code`/`dhis2-name`
properties that state the instance's own spelling beside the published one.

That pair is the whole recoverability contract of the substitute posture: whatever the guide
publishes, the DHIS2 spelling behind it is one property away, machine-readable, in the same
document. The translation extension is not that carrier - it publishes the same rewrite the primary
element does, because a resource whose `title` and whose `_title` disagreed would be stating two
different names for one object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from dhis2w_fhir.r4 import Extension

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "CODE_SUBSTITUTION_SEPARATOR",
    "DHIS2_CODE_PROPERTY",
    "DHIS2_ID_PROPERTY",
    "DHIS2_NAME_PROPERTY",
    "CodeCollision",
    "CodeSubstituter",
    "CodeSubstitutionCollisionError",
    "CodeSubstitutions",
    "CodedProjectionIn",
    "OriginalSpellingExtensionUrls",
    "carries_spaced_code",
    "carries_substitutable_code",
    "code_collisions",
    "code_substitutions",
    "original_spelling_extensions",
    "published_codes",
    "substituted_code",
]

#: What a space in a published code becomes. A hyphen is valid in every FHIR `code`, `id`, and URL
#: segment, so one separator serves the concept code, the identity stem, and the anchor alike.
CODE_SUBSTITUTION_SEPARATOR = "-"

#: The concept property carrying the DHIS2 code byte-true, whatever code the guide published for it.
DHIS2_CODE_PROPERTY = "dhis2-code"

#: The concept property carrying the DHIS2 UID, written when the concept code is the DHIS2 code.
DHIS2_ID_PROPERTY = "dhis2-id"

#: The concept property carrying the DHIS2 name byte-true, written when the guide published a
#: rewritten display above it. The twin of `dhis2-code`, for the same reason: a consumer that reads
#: a rewritten spelling needs the instance's own spelling to search the instance for.
DHIS2_NAME_PROPERTY = "dhis2-name"


class CodedProjectionIn(BaseModel):
    """A DHIS2 projection whose code or name a substitute-posture run may publish in rewritten form."""

    code: str | None = None
    """The code the guide publishes, which is the DHIS2 code unless this run rewrote it."""

    original_code: str | None = None
    """The DHIS2 code byte-true, set only when this run published a rewritten code above it."""

    original_name: str | None = None
    """The DHIS2 name byte-true, set only when this run published rewritten wording above it."""

    original_form_name: str | None = None
    """The DHIS2 form name byte-true, set only when this run published rewritten wording above it."""

    @property
    def dhis2_code(self) -> str | None:
        """The code DHIS2 holds: the original when this run rewrote it, else the published code."""
        return self.original_code if self.original_code is not None else self.code

    @property
    def dhis2_name(self) -> str | None:
        """The name DHIS2 holds, or None when this run published the name byte-true."""
        return self.original_name

    @property
    def dhis2_form_name(self) -> str | None:
        """The form name DHIS2 holds, or None when this run published the form name byte-true."""
        return self.original_form_name


class OriginalSpellingExtensionUrls(BaseModel):
    """The two extension definitions a resource states the DHIS2 spellings of its code and name under."""

    model_config = ConfigDict(frozen=True)

    code_url: str
    """Canonical URL of the `D2OriginalCode` StructureDefinition the guide defines."""

    name_url: str
    """Canonical URL of the `D2OriginalName` StructureDefinition the guide defines."""


def original_spelling_extensions(urls: OriginalSpellingExtensionUrls, projection: CodedProjectionIn) -> list[Extension]:
    """The `D2OriginalCode`/`D2OriginalName` extensions one resource carries where the run rewrote its spelling.

    A concept states its instance spellings as concept properties; a resource whose whole identity
    is one DHIS2 object states them as the two extensions the guide's foundation layer defines for
    exactly that, so every extension a published resource carries resolves to a definition the guide
    ships. Empty when the run published both spellings byte-true, which is every run under the
    `refuse` posture and most objects under `substitute`.
    """
    stated = ((urls.code_url, projection.original_code), (urls.name_url, projection.original_name))
    return [Extension(url=url, valueString=value) for url, value in stated if value is not None]


#: The comparison characters a substitute-posture run rewords in a code. Only `<` aborts the IG
#: publisher's build (identifier values are written into a table cell unescaped and strict-parsed
#: afterwards, and `<` opens a tag - the character `dhis2w_fhir.validation.build_aborting_code`
#: grades), but both are reworded, for the reason `dhis2w_fhir.validation.substitution` gives for
#: names: a guide whose bands read "under-1500g" and ">10-sec" states one fact in two vocabularies,
#: and the code would disagree with the name the same run publishes beside it.
_REWORDED_CODE_CHARACTERS = "<>"


def _carries_comparison(code: str) -> bool:
    """Whether one code carries a `<` or a `>`, which a substitute-posture run rewords."""
    return any(character in code for character in _REWORDED_CODE_CHARACTERS)


def carries_spaced_code(value: str | None) -> bool:
    """Whether one code carries a space - what the `spaced-code` finding reports, whatever the posture."""
    return value is not None and " " in value


def carries_substitutable_code(value: str | None) -> bool:
    """Whether one code carries a space, a `<` or a `>`, which is what the substitute posture rewrites."""
    return value is not None and (" " in value or _carries_comparison(value))


def substituted_code(code: str) -> str:
    """One code as the substitute posture publishes it, before any de-collision the run has to apply.

    A comparison is reworded the way a name's is (`< 6 Months` reads `under 6 Months`, `>10 sec`
    reads `over 10 sec`), which consumes every `<` and `>`, and then every space is hyphenated, so
    `ENTO - IRS < 6 Months` publishes as `ENTO---IRS-under-6-Months` and `>10 sec` as `over-10-sec`.
    The DHIS2 code rides beside it as the `dhis2-code` property either way.
    """
    # Imported here rather than at the top: the validation package imports this module, and a
    # module-level import of its `substitution` submodule would run that package's `__init__` first.
    from dhis2w_fhir.validation.substitution import substitute_build_aborting_text

    reworded = substitute_build_aborting_text(code) if _carries_comparison(code) else code
    return reworded.replace(" ", CODE_SUBSTITUTION_SEPARATOR)


class CodeSubstitutions(BaseModel):
    """Every DHIS2 code one run published in rewritten form, keyed by the code it published.

    The identifier terminology enumerates its concepts off the ConceptMaps rather than off the
    projections, so it reads this to state each rewritten concept's DHIS2 code beside it.
    """

    model_config = ConfigDict(frozen=True)

    originals_by_published: dict[str, str] = Field(default_factory=dict)

    def original_for(self, published: str) -> str | None:
        """The DHIS2 code behind one published code, or None when the run published it byte-true."""
        return self.originals_by_published.get(published)


def code_substitutions(models: Iterable[BaseModel]) -> CodeSubstitutions:
    """Collect every code rewrite the given projections carry, sorted by the published code."""
    gathered: dict[str, str] = {}
    for model in models:
        _gather(model, gathered)
    return CodeSubstitutions(originals_by_published=dict(sorted(gathered.items())))


class CodeCollision(BaseModel):
    """Two DHIS2 codes a substitute-posture run would publish as one code."""

    model_config = ConfigDict(frozen=True)

    published: str
    """The code both would publish as."""

    rewritten: str
    """The DHIS2 code the run rewrites onto `published`."""

    holder: str
    """The other DHIS2 code that already publishes as `published`: a literal code, or another rewrite."""


class CodeSubstitutionCollisionError(LookupError):
    """Raised when a substitute-posture run would publish two different DHIS2 codes as one code.

    DHIS2 holds no duplicate, so every collision is one the rewrite makes: `Pre eclampsia` onto a
    literal `Pre-eclampsia`, or `<5` and `under 5` onto one `under-5`. Publishing either one under a
    suffix would let a published code move from one object to another between runs - the selection
    grows, or the other code is cleaned in DHIS2 - and a system that stored it would silently point
    at the wrong object. The run is refused instead, naming both codes, so one of them is changed in
    DHIS2. A `LookupError` so the CLI's error funnel renders it as one message rather than a traceback.
    """

    def __init__(self, collisions: Sequence[CodeCollision]) -> None:
        """Hold every collision the run found and state them in one message."""
        self.collisions = tuple(collisions)
        stated = "; ".join(
            f"{collision.rewritten!r} and {collision.holder!r} would both publish as {collision.published!r}"
            for collision in self.collisions
        )
        super().__init__(
            f'hostile_names = "substitute" would publish two DHIS2 codes as one: {stated}. Change one code of '
            "each pair in DHIS2, then run `d2w fhir validate` for the full report."
        )


class CodeSubstituter:
    """The published code every DHIS2 code of one run carrying a space, `<` or `>` takes, and the collisions it makes.

    The published code is `substituted_code` of the DHIS2 code and nothing else, so it never depends
    on what else the run holds: adding a form to the selection or cleaning another code in DHIS2
    leaves it as it was. Where two DHIS2 codes would publish as one, `collisions` names them, and the
    gate refuses the run once it has decided to substitute.
    """

    def __init__(self) -> None:
        """Start a run that has observed no code."""
        self._observed: set[str] = set()

    def observe(self, model: BaseModel) -> None:
        """Register every code one projection carries, so `collisions` reads the run's whole pool."""
        if isinstance(model, CodedProjectionIn) and model.code is not None:
            self._observed.add(model.code)
        for field_name in type(model).model_fields:
            self._observe_value(getattr(model, field_name))

    def observe_code(self, code: str) -> None:
        """Register one DHIS2 code the run holds, so `collisions` reads it."""
        self._observed.add(code)

    def published_for(self, code: str) -> str:
        """The code the guide publishes in one DHIS2 code's place, byte-true unless it carries a space, `<` or `>`."""
        self._observed.add(code)
        return substituted_code(code) if carries_substitutable_code(code) else code

    def collisions(self) -> list[CodeCollision]:
        """Every pair of observed DHIS2 codes that would publish as one code, in sorted order.

        Literal codes hold their own spelling first; the rewrites are then placed in sorted order, so
        the pair a collision names does not depend on the order the projections were walked in.
        """
        holders = {code: code for code in self._observed if not carries_substitutable_code(code)}
        found: list[CodeCollision] = []
        for original in sorted(code for code in self._observed if carries_substitutable_code(code)):
            published = substituted_code(original)
            holder = holders.setdefault(published, original)
            if holder != original:
                found.append(CodeCollision(published=published, rewritten=original, holder=holder))
        return found

    def _observe_value(self, value: object) -> None:
        """Walk one field's value for nested projections, whatever container it arrives in."""
        if isinstance(value, BaseModel):
            self.observe(value)
        elif isinstance(value, list):
            for member in value:
                self._observe_value(member)
        elif isinstance(value, dict):
            for member in value.values():
                self._observe_value(member)


def published_codes(codes: Sequence[str | None], *, substituting: bool) -> dict[str, str]:
    """The code the guide publishes in each DHIS2 code's place, as one run's gate publishes them.

    The offline reading of a screening, for a caller that grades what a generate run would publish
    without emitting anything: `d2w fhir validate` resolves an identity stem off a code, and the code
    a stem is read off is the published one. A run that is not substituting publishes every code as
    DHIS2 states it, so the answer is every code mapped to itself.
    """
    stated = [code for code in codes if code is not None]
    if not substituting:
        return {code: code for code in stated}
    substituter = CodeSubstituter()
    return {code: substituter.published_for(code) for code in stated}


def code_collisions(codes: Sequence[str | None]) -> list[CodeCollision]:
    """Every pair of the given DHIS2 codes a substitute-posture run would publish as one code."""
    substituter = CodeSubstituter()
    for code in codes:
        if code is not None:
            substituter.observe_code(code)
    return substituter.collisions()


def _gather(model: BaseModel, gathered: dict[str, str]) -> None:
    """Record one projection's own rewrite, then walk everything nested under it."""
    if isinstance(model, CodedProjectionIn) and model.code is not None and model.original_code is not None:
        gathered.setdefault(model.code, model.original_code)
    for field_name in type(model).model_fields:
        _gather_value(getattr(model, field_name), gathered)


def _gather_value(value: object, gathered: dict[str, str]) -> None:
    """Walk one field's value for nested projections, whatever container it arrives in."""
    if isinstance(value, BaseModel):
        _gather(value, gathered)
    elif isinstance(value, list):
        for member in value:
            _gather_value(member, gathered)
    elif isinstance(value, dict):
        for member in value.values():
            _gather_value(member, gathered)
