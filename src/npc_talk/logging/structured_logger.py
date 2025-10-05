from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from .events import Event


class StructuredLogger:
    """Write structured events to a JSON Lines file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._file = self._prepare_file(path)

    @staticmethod
    def _prepare_file(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("w", encoding="utf-8")

    def handle(self, event: Event) -> None:
        record = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._lock:
            self._file.write(record + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()

    @property
    def path(self) -> Path:
        return self._path
