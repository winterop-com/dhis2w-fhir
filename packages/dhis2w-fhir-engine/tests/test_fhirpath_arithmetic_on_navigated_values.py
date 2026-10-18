"""Tests for FHIRPath arithmetic on values navigated out of a resource rather than written as literals."""

from decimal import Decimal
from typing import Any

import pytest

from dhis2w_fhir_engine import FHIRPathEvaluator, unwrap_primitives

#: A questionnaire response whose answers are what an aggregate form's values arrive as.
RESPONSE: dict[str, Any] = {
    "resourceType": "QuestionnaireResponse",
    "status": "completed",
    "item": [
        {"linkId": "first", "answer": [{"valueInteger": 3}]},
        {"linkId": "second", "answer": [{"valueInteger": 4}]},
        {"linkId": "third", "answer": [{"valueDecimal": 2.5}]},
        {"linkId": "name", "answer": [{"valueString": "Village"}]},
    ],
}


def _evaluate(expression: str) -> list[Any]:
    """Evaluate one expression against the response and unwrap what it answers."""
    unwrapped: list[Any] = unwrap_primitives(FHIRPathEvaluator().evaluate(expression, RESPONSE))
    return unwrapped


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("item.where(linkId = 'first').answer.value + 1", [Decimal(4)]),
        ("1 + item.where(linkId = 'first').answer.value", [Decimal(4)]),
        ("item.where(linkId = 'second').answer.value - item.where(linkId = 'first').answer.value", [Decimal(1)]),
        ("item.where(linkId = 'second').answer.value * 2", [Decimal(8)]),
        ("item.where(linkId = 'second').answer.value / 2", [Decimal(2)]),
        ("item.where(linkId = 'second').answer.value div 3", [1]),
        ("item.where(linkId = 'second').answer.value mod 3", [1]),
        ("-item.where(linkId = 'first').answer.value", [-3]),
        ("item.where(linkId = 'name').answer.value + ' North'", ["Village North"]),
        ("item.where(linkId = 'name').answer.value & ' North'", ["Village North"]),
    ],
)
def test_an_operator_acts_on_a_navigated_value_as_on_the_literal_it_holds(expression: str, expected: list[Any]) -> None:
    """A value read out of the resource takes part in arithmetic exactly as the same literal does."""
    assert _evaluate(expression) == expected


def test_aggregate_sums_navigated_values_as_sum_does() -> None:
    """`aggregate($this + $total, 0)` over a response's answers equals `sum()` over the same answers."""
    aggregated = _evaluate("item.answer.value.ofType(integer).aggregate($this + $total, 0)")
    summed = _evaluate("item.answer.value.ofType(integer).sum()")

    assert aggregated == [Decimal(7)]
    assert [Decimal(str(value)) for value in summed] == aggregated
