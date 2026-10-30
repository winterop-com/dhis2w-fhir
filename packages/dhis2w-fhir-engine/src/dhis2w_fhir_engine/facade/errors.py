"""The failure a user can fix, carrying one diagnostic per thing that is wrong and depending on no terminal."""

from __future__ import annotations


class FhirUserError(Exception):
    """An expected failure that is the caller's to fix, holding its diagnostics in the order they read."""

    def __init__(self, *diagnostics: str) -> None:
        """Hold the diagnostics in order; `str()` joins them with newlines."""
        self.diagnostics: tuple[str, ...] = diagnostics
        super().__init__("\n".join(diagnostics))
