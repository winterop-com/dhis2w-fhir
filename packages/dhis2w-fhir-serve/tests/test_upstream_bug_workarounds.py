"""The workaround-works halves of BUGS.md #115 and #116 in dhis2w: the capture server's poll.

The bug-still-present halves live in dhis2w, against the client alone.
"""

from __future__ import annotations

import pytest
from dhis2w_client.errors import Dhis2ApiError


@pytest.mark.upstream_bug
def test_bug_115_workaround_enrollment_poll_orders_by_enrolled_at() -> None:
    """BUGS.md #115 — workaround-works: the fhir-serve enrollment poll orders by `enrolledAt`, never `createdAt`."""
    from dhis2w_fhir_serve.register.wire import ENROLLMENT_POLL_ORDER, POLL_ORDER

    assert ENROLLMENT_POLL_ORDER == "enrolledAt:asc"
    assert POLL_ORDER == "createdAt:asc", "the tracked entity poll keeps creation order; only enrollments diverge"


@pytest.mark.upstream_bug
def test_bug_116_workaround_poll_recognises_the_refusal() -> None:
    """BUGS.md #116 — workaround-works: the fhir-serve poll recognises exactly this refusal and no other 409."""
    from dhis2w_fhir_serve.register.wire import _is_tombstone_read_syntax_refusal

    refused = Dhis2ApiError(
        status_code=409,
        message="",
        body={
            "message": "Query failed because of a syntax error (SqlState: 42601)",
            "devMessage": 'ERROR: trailing junk after numeric literal at or near "1903ORDER"',
        },
    )
    other = Dhis2ApiError(status_code=409, message="", body={"message": "Data value not found or not accessible"})
    assert _is_tombstone_read_syntax_refusal(refused)
    assert not _is_tombstone_read_syntax_refusal(other)
