from __future__ import annotations

import asyncio
import logging
from logging import Logger
from pathlib import Path

from npc_talk.app import run_demo
from npc_talk.config import project_root


def _setup_logger() -> Logger:
    root = project_root()
    log_path = root / "run.log"
    logger = logging.getLogger("npc_talk_demo")
    logger.setLevel(logging.INFO)
    # Replace handlers
    for h in list(logger.handlers):
        logger.removeHandler(h)
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fmt = logging.Formatter("%(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # Console minimal
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)
    return logger


def main() -> None:
    print("============================================================")
    print("NPC Talk Demo (Agentscope) [packaged]")
    print("============================================================")
    logger = _setup_logger()
    try:
        asyncio.run(run_demo(logger=logger))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

