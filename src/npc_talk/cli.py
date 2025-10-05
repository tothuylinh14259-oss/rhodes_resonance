from __future__ import annotations

import asyncio
from typing import Optional

from npc_talk.app import run_demo
from npc_talk.logging import LoggingContext, create_logging_context


def main() -> None:
    print("============================================================")
    print("NPC Talk Demo (Agentscope) [packaged]")
    print("============================================================")
    log_ctx: Optional[LoggingContext] = None
    try:
        log_ctx = create_logging_context()
        asyncio.run(run_demo(log_ctx=log_ctx))
    except KeyboardInterrupt:
        pass
    finally:
        if log_ctx:
            log_ctx.close()


if __name__ == "__main__":
    main()
