"""Step 1: evaluate an expression. Everything the engine does starts from FHIR-shaped data.

The evaluator knows nothing about DHIS2, so this step needs no instance, no guide, and no network. Step 6 and
`examples/engine/` go further; this is the piece the rest of the path sits beside.

Usage:
    uv run python examples/facade/step1_evaluate.py

"""

from __future__ import annotations

from dhis2w_fhir_engine import FHIRPathEvaluator, unwrap_primitives

PATIENT = {"resourceType": "Patient", "id": "p1", "name": [{"given": ["Aminata"], "family": "Kamara"}]}


def main() -> None:
    """Ask one resource one question."""
    given = FHIRPathEvaluator().evaluate("Patient.name.given", PATIENT)
    print("given name:", unwrap_primitives(given))


if __name__ == "__main__":
    main()
