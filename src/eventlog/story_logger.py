from __future__ import annotations

from pathlib import Path
from threading import Lock

from .events import Event, EventType


class StoryLogger:
    """Persist human-readable narrative lines extracted from events.

    This logger is intentionally opinionated: it keeps the core story flow
    (dialogues/narration and action results) and filters out meta prompts
    like per-turn recaps and round banners to avoid perceived duplicates
    in the human-readable story log. Structured logs remain full-fidelity.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._file = self._prepare_file(path)
        # Keep only the first world-summary (opening background); subsequent
        # summaries are repetitive for human readers.
        self._printed_initial_world_summary = False

    @staticmethod
    def _prepare_file(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("w", encoding="utf-8")

    def handle(self, event: Event) -> None:
        # Only record human-facing narrative lines
        if event.event_type is not EventType.NARRATIVE:
            return

        # Filter out meta narrative that causes duplication/noise in story log
        phase = (event.phase or "").strip()
        if phase.startswith("context:"):
            # e.g. pre-turn recap blocks
            return
        if phase == "round-start":
            # e.g. "第N回合：小队行动" banners
            return
        if phase == "world-summary":
            # keep only the first world summary as the opening background
            if self._printed_initial_world_summary:
                return
            self._printed_initial_world_summary = True

        text = event.data.get("text", "")
        actor = event.actor or "system"
        timestamp = event.timestamp.isoformat() if event.timestamp else ""
        line = f"[{event.event_id}] {timestamp} {actor}: {text}"
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()

    @property
    def path(self) -> Path:
        return self._path
