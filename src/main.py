#!/usr/bin/env python3
from __future__ import annotations

"""
Central Orchestrator

`main.py` 作为“中心”，负责：
- 定义并注入可用工具到各子模块（agents 不依赖 world）；
- 组装日志、配置、运行入口；
- 将工具分发表传入运行引擎（npc_talk.app.run_demo）。

运行方式：
  python src/main.py
或：
  python -m npc_talk.cli  （仍可用的打包入口，调用相同引擎）
"""

import asyncio
from typing import Dict

from runtime.engine import run_demo
from actions.npc import (
    describe_world as tool_describe_world,
    perform_attack,
    auto_engage,
    perform_skill_check,
    advance_position,
    adjust_relation,
    transfer_item,
)
from eventlog import LoggingContext, create_logging_context


def _build_tool_dispatch() -> Dict[str, object]:
    return {
        "describe_world": tool_describe_world,
        "perform_attack": perform_attack,
        "perform_skill_check": perform_skill_check,
        "advance_position": advance_position,
        "adjust_relation": adjust_relation,
        "transfer_item": transfer_item,
        "auto_engage": auto_engage,
    }


def _tool_list() -> list[object]:
    # 供 agents 工具箱注册使用
    return [
        tool_describe_world,
        perform_attack,
        auto_engage,
        perform_skill_check,
        advance_position,
        adjust_relation,
        transfer_item,
    ]


def main() -> None:
    print("============================================================")
    print("NPC Talk Demo (Orchestrator: main.py)")
    print("============================================================")
    log_ctx: LoggingContext | None = None
    try:
        log_ctx = create_logging_context()
        asyncio.run(
            run_demo(
                log_ctx=log_ctx,
                tool_fns=_tool_list(),
                tool_dispatch=_build_tool_dispatch(),
            )
        )
    except KeyboardInterrupt:
        pass
    finally:
        if log_ctx:
            log_ctx.close()


if __name__ == "__main__":
    main()
