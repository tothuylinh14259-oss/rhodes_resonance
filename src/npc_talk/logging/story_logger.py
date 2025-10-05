from __future__ import annotations

from pathlib import Path
from threading import Lock

from .events import Event, EventType


class StoryLogger:
    """Persist human-readable narrative lines extracted from events."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._file = self._prepare_file(path)

    @staticmethod
    def _prepare_file(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("w", encoding="utf-8")

    def handle(self, event: Event) -> None:
        if event.event_type is not EventType.NARRATIVE:
            return
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
