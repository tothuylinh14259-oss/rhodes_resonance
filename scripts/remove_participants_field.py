#!/usr/bin/env python3
"""
One-off migration: remove the top-level `participants` key from configs/characters.json.
Creates a sibling backup file: configs/characters.backup.participants.json
Idempotent: if the key is absent, it does nothing (still writes no changes).
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
conf = ROOT / "configs" / "characters.json"
bak = ROOT / "configs" / "characters.backup.participants.json"

if not conf.exists():
    raise SystemExit(f"not found: {conf}")

text = conf.read_text(encoding="utf-8")
try:
    data = json.loads(text)
except Exception as e:
    raise SystemExit(f"invalid JSON in {conf}: {e}")

if "participants" in data:
    # Write backup
    bak.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Remove and write back
    data.pop("participants", None)
    conf.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("removed: participants (backup written)")
else:
    print("no participants key present; nothing changed")
