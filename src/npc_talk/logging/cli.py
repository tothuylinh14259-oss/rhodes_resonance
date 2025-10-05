from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

from npc_talk.config import project_root


def _iter_events(path: Path) -> Iterator[Dict[str, object]]:
    if not path.exists():
        return iter(())
    def _read() -> Iterator[Dict[str, object]]:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    return _read()


def _match(record: Dict[str, object], *, actor: Optional[str], turn: Optional[int], event_type: Optional[str], phase: Optional[str]) -> bool:
    if actor and record.get("actor") != actor:
        return False
    if turn is not None and record.get("turn") != turn:
        return False
    if event_type and record.get("event_type") != event_type:
        return False
    if phase and record.get("phase") != phase:
        return False
    return True


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Filter structured NPC talk events")
    parser.add_argument(
        "--file",
        default=str(project_root() / "logs" / "run_events.jsonl"),
        help="Path to the events JSONL file (default: %(default)s)",
    )
    parser.add_argument("--actor", help="Filter by actor name")
    parser.add_argument("--turn", type=int, help="Filter by turn number")
    parser.add_argument(
        "--event-type",
        dest="event_type",
        help="Filter by event type (e.g. turn_start, tool_call, narrative)",
    )
    parser.add_argument("--phase", help="Filter by phase label")
    parser.add_argument("--limit", type=int, help="Stop after printing N records")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(list(argv) if argv is not None else None)

    path = Path(args.file)
    events = _iter_events(path)
    count = 0
    for record in events:
        if not _match(record, actor=args.actor, turn=args.turn, event_type=args.event_type, phase=args.phase):
            continue
        count += 1
        if args.pretty:
            print(json.dumps(record, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(record, ensure_ascii=False))
        if args.limit and count >= args.limit:
            break
    if count == 0:
        print("<no events matched>")


if __name__ == "__main__":
    main()
