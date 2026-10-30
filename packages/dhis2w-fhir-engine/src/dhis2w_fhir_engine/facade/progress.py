"""How a long run reports its numbered steps, to whatever display its caller holds."""

from __future__ import annotations

from typing import Protocol


class ProgressReporter(Protocol):
    """What a display implements to be told about a run's steps."""

    def step(self, index: int, total: int, label: str) -> None:
        """Announce that step `index` of `total`, named `label`, is starting."""
        ...

    def complete(self, index: int, total: int, label: str, summary: str, *, style: str | None = None) -> None:
        """Announce that step `index` finished, with a one-line `summary`."""
        ...


class StepAnnouncer:
    """Announces a run's numbered steps to a progress reporter, or to nothing when none was passed.

    A step opens with `step`, narrates itself with as many `tick` captions as it likes, and closes
    with exactly one `complete`. A tick is a caption an animated display overwrites in place, so a
    fifty-page organisation-unit walk costs no more output than a one-page one and a plain reporter
    renders none of it; a completion is the durable `[k/N] label: summary` line, so it fires once
    per numbered step and never inside one - the counter an animated display advances on completion
    would otherwise run past the run's own length. `start`, `finish`, and `stop` bound the whole run
    and belong to the caller that built the reporter, not to the service.
    """

    def __init__(self, reporter: ProgressReporter | None = None, total: int = 0) -> None:
        """Store the reporter to announce to and how many steps the run holds."""
        self._reporter = reporter
        self._total = total
        self._index = 0
        self._label = ""

    def step(self, label: str, caption: str | None = None) -> None:
        """Open the next step: `label` names it in its completion line, `caption` in the live display."""
        self._index += 1
        self._label = label
        if self._reporter is not None:
            self._reporter.step(self._index, self._total, caption or label)

    def tick(self, caption: str) -> None:
        """Re-caption the step already running, with no durable line and no move of the counter."""
        if self._reporter is not None:
            self._reporter.step(self._index, self._total, caption)

    def complete(self, summary: str) -> None:
        """Close the step already running with the one-line outcome it is reported by."""
        if self._reporter is not None:
            self._reporter.complete(self._index, self._total, self._label, summary)
