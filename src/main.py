#!/usr/bin/env python3
"""
Shim entrypoint kept for backward compatibility.
New code lives under the `npc_talk` package; prefer:
  python -m npc_talk.cli
or
  python -m npc_talk
"""
from __future__ import annotations

from npc_talk.cli import main


if __name__ == "__main__":
    main()
