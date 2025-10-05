from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from npc_talk.config import project_root

from .bus import EventBus
from .story_logger import StoryLogger
from .structured_logger import StructuredLogger


@dataclass
class LoggingContext:
    bus: EventBus
    structured: StructuredLogger
    story: StoryLogger

    def close(self) -> None:
        self.structured.close()
        self.story.close()


def create_logging_context(base_path: Optional[Path] = None) -> LoggingContext:
    root = base_path or project_root()
    logs_dir = root / "logs"
    events_path = logs_dir / "run_events.jsonl"
    story_path = logs_dir / "run_story.log"

    bus = EventBus()
    structured = StructuredLogger(events_path)
    story = StoryLogger(story_path)

    bus.subscribe(structured.handle)
    bus.subscribe(story.handle)

    return LoggingContext(bus=bus, structured=structured, story=story)
