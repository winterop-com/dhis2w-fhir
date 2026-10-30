"""A pages fixture whose every DHIS2 object carries translations, and the baseline pages it renders without them.

`data/pages-baseline/` holds the pages this fixture rendered to before the pages listed any
translation, so a run with `translations_on_pages = false` is held to them byte for byte. The
fixture uses only projection models, which is what lets the baseline be rendered again by any
revision of the renderer with `write_baseline(directory)` from this module.
"""

from __future__ import annotations

from pathlib import Path

from dhis2w_fhir.resources.pages.builders import PAGES_DIRECTORY, build_page_artifacts
from dhis2w_fhir.resources.pages.schemas import PagesIn
from dhis2w_fhir_engine.facade.config import GenerateConfig
from dhis2w_fhir_engine.facade.i18n import TranslationIn
from dhis2w_fhir_engine.facade.resources.examples.schemas import SyntheticPlacement
from dhis2w_fhir_engine.facade.resources.option_sets.schemas import OptionIn, OptionSetIn
from dhis2w_fhir_engine.facade.resources.organisation_units.schemas import (
    OrganisationUnitIn,
    OrganisationUnitLevelIn,
    OrganisationUnitLevelNames,
)
from dhis2w_fhir_engine.facade.resources.questionnaires.schemas import (
    ProgramContextIn,
    QuestionnaireItemIn,
    QuestionnaireSectionIn,
    QuestionnaireSourceIn,
)

#: Where the committed baseline pages live.
BASELINE_DIRECTORY = Path(__file__).parent / "data" / "pages-baseline"

CANONICAL = "http://example.org/fhir"


def _name(locale: str, value: str) -> TranslationIn:
    """One NAME translation."""
    return TranslationIn(locale=locale, property="NAME", value=value)


def _form_name(locale: str, value: str) -> TranslationIn:
    """One FORM_NAME translation."""
    return TranslationIn(locale=locale, property="FORM_NAME", value=value)


#: A question asked under its form name: its FORM_NAME translation is the one the page lists.
FORM_NAMED_QUESTION = QuestionnaireItemIn(
    uid="De1aaaaaaaa",
    name="Deaths total",
    form_name="Deaths",
    value_type="INTEGER",
    translations=[_name("lo", "ການເສຍຊີວິດທັງໝົດ"), _form_name("lo", "ການເສຍຊີວິດ")],
)

#: A question asked under its name, translated with the two characters a markdown table cell must escape.
NAMED_QUESTION = QuestionnaireItemIn(
    uid="De2aaaaaaaa",
    name="Weight",
    value_type="NUMBER",
    translations=[_name("lo", "ນ້ຳໜັກ < 2.5 | kg"), _name("en", "Weight")],
)

#: A question with no translation, which the intro's question table leaves out.
UNTRANSLATED_QUESTION = QuestionnaireItemIn(uid="De3aaaaaaaa", name="Comment", value_type="LONG_TEXT")

DATA_SET = QuestionnaireSourceIn(
    uid="YFTk3VdO9av",
    name="Mortality < 5 years",
    code="DS_MORT",
    description="Deaths under five.",
    kind="aggregate",
    period_type="Monthly",
    translations=[
        _name("lo", "ອັດຕາການຕາຍ < 5 ປີ"),
        _name("fr", "Mortalité < 5 ans"),
        _name("en", "Mortality < 5 years"),
    ],
    sections=[
        QuestionnaireSectionIn(
            uid="Sec1aaaaaaa",
            name="Deaths | reported",
            translations=[_name("lo", "ການເສຍຊີວິດ")],
            items=[FORM_NAMED_QUESTION, NAMED_QUESTION],
        )
    ],
    flat_items=[UNTRANSLATED_QUESTION],
)

_CHILD_PROGRAMME = ProgramContextIn(uid="IpHINAT79UW", name="Child Programme", translations=[_name("lo", "ໂຄງການເດັກ")])

BIRTH_STAGE = QuestionnaireSourceIn(
    uid="A03MvHHogjR",
    name="Birth",
    kind="tracker-event",
    program=_CHILD_PROGRAMME,
    translations=[_name("lo", "ການເກີດ")],
    flat_items=[
        QuestionnaireItemIn(
            uid="a3kGcGDCuk6",
            name="Apgar Score",
            value_type="INTEGER",
            translations=[_name("lo", "ຄະແນນ Apgar")],
        )
    ],
)

OPTION_SET = OptionSetIn(
    uid="Os1aaaaaaaa",
    name="Sex",
    translations=[_name("lo", "ເພດ")],
    options=[
        OptionIn(uid="Op1aaaaaaaa", name="Female", code="F", sort_order=1, translations=[_name("lo", "ຍິງ")]),
        OptionIn(uid="Op2aaaaaaaa", name="Male", code="M", sort_order=2, translations=[_name("en", "Male")]),
    ],
)

DESCRIBED_OPTION_SET = OptionSetIn(
    uid="Os2aaaaaaaa",
    name="Birth type",
    description="How a birth is reported.",
    options=[OptionIn(uid="Op3aaaaaaaa", name="Single", sort_order=1)],
)

ROOT_UNIT = OrganisationUnitIn(
    uid="ImspTQPwCqd",
    name="Lao PDR",
    level=1,
    path="/ImspTQPwCqd",
    description="The national root unit.",
    translations=[_name("lo", "ສປປ ລາວ")],
)

CHILD_UNIT = OrganisationUnitIn(
    uid="O6uvpzGd5pu",
    name="Vientiane Capital",
    level=2,
    path="/ImspTQPwCqd/O6uvpzGd5pu",
    parent_uid="ImspTQPwCqd",
    translations=[_name("lo", "ນະຄອນຫຼວງວຽງຈັນ")],
)

LEVELS = OrganisationUnitLevelNames(
    levels=[
        OrganisationUnitLevelIn(level=1, name="Country", translations=[_name("lo", "ປະເທດ")]),
        OrganisationUnitLevelIn(level=2, name="Province"),
    ]
)


def translated_pages_input() -> PagesIn:
    """Every object of the fixture, translated."""
    return PagesIn(
        forms=[DATA_SET, BIRTH_STAGE],
        option_sets=[OPTION_SET, DESCRIBED_OPTION_SET],
        organisation_units=[ROOT_UNIT, CHILD_UNIT],
        organisation_unit_levels=LEVELS,
    )


def render_pages(pages: PagesIn, config: GenerateConfig) -> dict[str, str]:
    """Build the page artifacts of one input and index them by file name."""
    placements = {form.uid: SyntheticPlacement(organisation_unit_uids=(ROOT_UNIT.uid,)) for form in pages.forms}
    build = build_page_artifacts(pages, config, CANONICAL, example_placements=placements)
    return {
        artifact.relative_path.removeprefix(f"{PAGES_DIRECTORY}/"): artifact.content for artifact in build.artifacts
    }


def baseline_pages() -> dict[str, str]:
    """The committed baseline, indexed by file name."""
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(BASELINE_DIRECTORY.iterdir())}


def write_baseline(directory: Path) -> None:
    """Render the fixture with the default configuration into `directory`, one file per page."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in render_pages(translated_pages_input(), GenerateConfig()).items():
        (directory / name).write_text(content, encoding="utf-8")
