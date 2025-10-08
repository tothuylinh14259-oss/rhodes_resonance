#!/usr/bin/env python3
from __future__ import annotations

"""
Central Orchestrator (main layer)

职责：
- 加载配置、创建日志上下文；
- 构造 world 端口、actions 工具、agents 工厂；
- 通过依赖注入调用 runtime.engine.run_demo。
"""

import asyncio
from typing import Dict, Any
from dataclasses import asdict, is_dataclass

from runtime.engine import run_demo
from actions.npc import make_npc_actions
import world.tools as world_impl
from eventlog import create_logging_context, Event, EventType
from settings.loader import (
    project_root,
    load_prompts,
    load_model_config,
    load_feature_flags,
    load_story_config,
    load_characters,
)
from agents.factory import make_kimi_npc


class _WorldPort:
    """Light adapter around world.tools to avoid component coupling in engine."""

    # bind frequently used world functions as simple static methods
    set_dnd_character = staticmethod(world_impl.set_dnd_character)
    set_position = staticmethod(world_impl.set_position)
    set_scene = staticmethod(world_impl.set_scene)
    set_relation = staticmethod(world_impl.set_relation)
    get_turn = staticmethod(world_impl.get_turn)
    reset_actor_turn = staticmethod(world_impl.reset_actor_turn)
    end_combat = staticmethod(world_impl.end_combat)

    @staticmethod
    def snapshot() -> Dict[str, Any]:
        return world_impl.WORLD.snapshot()

    @staticmethod
    def runtime() -> Dict[str, Any]:
        W = world_impl.WORLD
        return {
            "positions": dict(W.positions),
            "in_combat": bool(W.in_combat),
            "turn_state": dict(W.turn_state),
            "round": int(W.round),
            "characters": dict(W.characters),
        }


def main() -> None:
    print("============================================================")
    print("NPC Talk Demo (Orchestrator: main.py)")
    print("============================================================")

    # Load configs
    prompts = load_prompts()
    model_cfg_obj = load_model_config()
    feature_flags = load_feature_flags()
    story_cfg = load_story_config()
    characters = load_characters()

    # Convert model config dataclass to mapping
    if is_dataclass(model_cfg_obj):
        model_cfg: Dict[str, Any] = asdict(model_cfg_obj)
    else:
        model_cfg = dict(getattr(model_cfg_obj, "__dict__", {}) or {})

    # Build logging context under project root
    root = project_root()
    log_ctx = create_logging_context(base_path=root)

    # Emit function adapter
    def emit(*, event_type: str, actor=None, phase=None, turn=None, data=None) -> None:
        ev = Event(event_type=EventType(event_type), actor=actor, phase=phase, turn=turn, data=dict(data or {}))
        log_ctx.bus.publish(ev)

    # Bind world and actions
    world = _WorldPort()
    tool_list, tool_dispatch = make_npc_actions(world=world_impl)

    # Agent builder
    def build_agent(name, persona, model_cfg, **kwargs):
        return make_kimi_npc(name, persona, model_cfg, **kwargs)

    try:
        asyncio.run(
            run_demo(
                emit=emit,
                build_agent=build_agent,
                tool_fns=tool_list,
                tool_dispatch=tool_dispatch,
                prompts=prompts,
                model_cfg=model_cfg,
                feature_flags=feature_flags,
                story_cfg=story_cfg,
                characters=characters,
                world=world,
            )
        )
    except KeyboardInterrupt:
        pass
    finally:
        log_ctx.close()


if __name__ == "__main__":
    main()
