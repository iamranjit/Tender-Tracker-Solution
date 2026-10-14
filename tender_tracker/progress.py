"""Live progress of a run, persisted on its RunLog row so any process (API, scheduler) can show it.

Writes are throttled (every ``interval`` seconds, or immediately for notable
events) and use their own short-lived session so they never interfere with
the pipeline's transaction.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .config import now_ist
from .storage import RunLog

log = logging.getLogger(__name__)

# (key, label, share of total run time) - shares are rough averages from real runs.
PHASES: list[tuple[str, str, float]] = [
    ("search", "Searching GeM", 0.55),
    ("store", "Saving & detecting changes", 0.02),
    ("documents", "Reading bid documents", 0.25),
    ("scoring", "Scoring & eligibility", 0.03),
    ("attachments", "Reading scope-of-work files", 0.12),
    ("notify", "Sending digest", 0.03),
]
_INDEX = {key: i for i, (key, _, _) in enumerate(PHASES)}
MAX_EVENTS = 12


class RunProgress:
    def __init__(self, session_factory: Any, run_id: int, interval: float = 2.0) -> None:
        self._Session = session_factory
        self.run_id = run_id
        self.interval = interval
        self._last_write = 0.0
        self._started = time.monotonic()
        self.state: dict[str, Any] = {
            "phase": "search",
            "phase_index": 0,
            "phases": [label for _, label, _ in PHASES],
            "done": 0,
            "total": 0,
            "message": "Starting…",
            "counters": {},
            "events": [],
            "percent": 0.0,
            "elapsed_seconds": 0,
            "eta_seconds": None,
        }

    # -- public API ------------------------------------------------------------------

    def phase(self, key: str, total: int = 0, message: str | None = None) -> None:
        self.state.update(phase=key, phase_index=_INDEX[key], done=0, total=total)
        self.event(message or PHASES[_INDEX[key]][1])

    def step(self, done: int | None = None, total: int | None = None, message: str | None = None, **counters: Any) -> None:
        if done is not None:
            self.state["done"] = done
        if total is not None:
            self.state["total"] = total
        if message:
            self.state["message"] = message
        self.state["counters"].update(counters)
        self._write()

    def event(self, message: str, **counters: Any) -> None:
        """A notable step: shown in the activity log and written immediately."""
        stamp = now_ist().strftime("%H:%M:%S")
        self.state["events"] = ([f"{stamp}  {message}"] + self.state["events"])[:MAX_EVENTS]
        self.state["message"] = message
        self.state["counters"].update(counters)
        self._write(force=True)

    def finish(self) -> None:
        self.state.update(percent=100.0, eta_seconds=0, message="Finished")
        self._write(force=True)

    # -- internals -------------------------------------------------------------------

    def _percent(self) -> float:
        idx = self.state["phase_index"]
        before = sum(share for _, _, share in PHASES[:idx])
        total = self.state["total"]
        fraction = min(1.0, self.state["done"] / total) if total else 0.0
        return round(min(99.0, (before + PHASES[idx][2] * fraction) * 100), 1)

    def _write(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < self.interval:
            return
        self._last_write = now
        elapsed = now - self._started
        pct = self._percent()
        self.state["percent"] = pct
        self.state["elapsed_seconds"] = int(elapsed)
        self.state["eta_seconds"] = int(elapsed / pct * (100 - pct)) if pct >= 3 else None
        try:
            with self._Session() as session:
                run = session.get(RunLog, self.run_id)
                if run is not None:
                    run.progress = dict(self.state)
                    run.heartbeat_at = now_ist()
                    session.commit()
        except Exception as exc:  # progress must never break a run
            log.debug("Progress write skipped: %s", exc)


class NullProgress(RunProgress):
    """Used when no database row exists (tests, ad-hoc calls)."""

    def __init__(self) -> None:  # noqa: D401 - no DB
        super().__init__(None, 0)

    def _write(self, force: bool = False) -> None:
        self.state["percent"] = self._percent()
