from __future__ import annotations

import json

from npc_talk.logging import Event, EventBus, EventType, StoryLogger, StructuredLogger


def test_event_serialisation_drops_none(tmp_path):
    bus = EventBus()
    structured = StructuredLogger(tmp_path / "events.jsonl")
    bus.subscribe(structured.handle)

    event = Event(
        event_type=EventType.ACTION,
        actor="Amiya",
        turn=1,
        data={"action": "attack", "target": "Mephisto", "result": None},
    )
    bus.publish(event)
    structured.close()

    content = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip()
    record = json.loads(content)
    assert record["actor"] == "Amiya"
    assert record["action"] == "attack"
    assert "result" not in record


def test_narrative_event_requires_text():
    bus = EventBus()
    event = Event(event_type=EventType.NARRATIVE, actor="Host", data={})
    try:
        bus.publish(event)
    except ValueError as exc:
        assert "missing required fields" in str(exc)
    else:  # pragma: no cover - emphasise failure path
        raise AssertionError("Narrative event without text should raise")


def test_story_logger_writes_human_log(tmp_path):
    bus = EventBus()
    story = StoryLogger(tmp_path / "story.log")
    bus.subscribe(story.handle)

    event = Event(event_type=EventType.NARRATIVE, actor="Host", data={"text": "Hello"})
    bus.publish(event)
    story.close()

    content = (tmp_path / "story.log").read_text(encoding="utf-8").strip()
    assert "Hello" in content
    assert "Host" in content
