#!/usr/bin/env python3
"""
Minimal NPC group chat + story driving demo (strict Agentscope version).
- Requires real `agentscope` with `agentscope.pipeline` present (no fallback).
- Demonstrates: MsgHub, sequential speaking, dynamic join, simple world tools.
Run (after conda env activation): python src/main.py
"""
from __future__ import annotations
import asyncio
import sys
import os

# Strict import: must use real agentscope.
from agentscope.pipeline import MsgHub, sequential_pipeline  # type: ignore
from agentscope.message import Msg  # type: ignore
from agentscope.agent import ReActAgent  # type: ignore
from agentscope.model import OpenAIChatModel  # type: ignore
from agentscope.formatter import OpenAIChatFormatter  # type: ignore
from agentscope.memory import InMemoryMemory  # type: ignore
from agentscope.tool import Toolkit  # type: ignore

from agents.npc import SimpleNPCAgent
from world.tools import WORLD, advance_time, change_relation


def banner():
    print("=" * 60)
    print("NPC Talk Demo (Agentscope) [real agentscope]")
    print("=" * 60)


# ---- Kimi (Moonshot) integration helpers ----
def make_kimi_npc(name: str, persona: str) -> ReActAgent:
    """Create an LLM-backed NPC using Kimi's OpenAI-compatible API.

    Required env vars (set in your conda env):
    - MOONSHOT_API_KEY: your Kimi API key
    - KIMI_BASE_URL: e.g. https://api.moonshot.cn/v1
    - KIMI_MODEL: e.g. kimi-k2-turbo-preview or moonshot-v1-128k
    """
    api_key = os.environ["MOONSHOT_API_KEY"]
    base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    model_name = os.getenv("KIMI_MODEL", "kimi-k2-turbo-preview")

    sys_prompt = (
        f"你是游戏中的NPC：{name}。人设：{persona}。"
        "规则：用简短中文发言，贴合人设，不要剧透世界规则；每次只说1-2句。"
    )

    model = OpenAIChatModel(
        model_name=model_name,
        api_key=api_key,
        stream=True,  # enable streaming output
        client_args={"base_url": base_url},
        generate_kwargs={"temperature": 0.7},
    )

    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=model,
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
        toolkit=Toolkit(),
    )


async def tavern_scene():
    # Use Kimi (Moonshot) LLM-backed NPCs
    warrior = make_kimi_npc("Warrior", "勇士，直来直去，讲究承诺与荣誉。")
    mage = make_kimi_npc("Mage", "法师，好奇健谈，喜欢引用古籍。")
    blacksmith = make_kimi_npc("Blacksmith", "铁匠，务实可靠，关心物价与原料。")

    async with MsgHub(
        participants=[warrior, mage],
        announcement=Msg("Host", "你们在酒馆壁炉旁相识。做个自我介绍。", "assistant"),
    ) as hub:
        await sequential_pipeline([warrior, mage])

        # Dynamic join
        hub.add(blacksmith)
        await hub.broadcast(Msg("Host", "铁匠走进酒馆，向你们打招呼。", "assistant"))
        await sequential_pipeline([blacksmith, mage, warrior])

        # World updates (could be tool calls by Director in a full setup)
        change_relation("Warrior", "Mage", +1, reason="合作愉快")
        advance_time(15)
        print("[system] world:", WORLD.snapshot())

        await hub.broadcast(Msg("Host", "夜深了，大家准备告辞。", "assistant"))


if __name__ == "__main__":
    banner()
    try:
        asyncio.run(tavern_scene())
    except KeyboardInterrupt:
        sys.exit(130)
