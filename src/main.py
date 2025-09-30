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
from agents.player import PlayerAgent
from agents.kp import KPAgent
from world.tools import WORLD, advance_time, change_relation, grant_item


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
        "规则：\n"
        "- 用简短中文发言，每次只说1-2句，贴合人设。\n"
        "- 当需要推进时间、调整关系或发放物品时，优先使用工具调用：\n"
        "  advance_time(mins:int)、change_relation(a:str,b:str,delta:int,reason:str)、grant_item(target:str,item:str,n:int)。\n"
        "- 工具调用完成后，再用一句话向对话对象说明处理结果。"
    )

    model = OpenAIChatModel(
        model_name=model_name,
        api_key=api_key,
        stream=True,  # enable streaming output
        client_args={"base_url": base_url},
        generate_kwargs={"temperature": 0.7},
    )

    # Equip world tools via a shared toolkit
    toolkit = Toolkit()
    toolkit.register_tool_function(advance_time)
    toolkit.register_tool_function(change_relation)
    toolkit.register_tool_function(grant_item)

    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=model,
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
        toolkit=toolkit,
    )


async def tavern_scene():
    # Use Kimi (Moonshot) LLM-backed NPCs + a human player + KP (GM)
    warrior = make_kimi_npc("Warrior", "勇士，直来直去，讲究承诺与荣誉。")
    mage = make_kimi_npc("Mage", "法师，好奇健谈，喜欢引用古籍。")
    blacksmith = make_kimi_npc("Blacksmith", "铁匠，务实可靠，关心物价与原料。")
    player = PlayerAgent(name="Player", prompt="你> ")
    kp = KPAgent(name="KP")

    async with MsgHub(
        participants=[warrior, mage, player, kp],
        announcement=Msg(
            "Host",
            "你们在酒馆壁炉旁相识。做个自我介绍。\n提示：玩家可直接发言；如需推进剧情，NPC 可使用工具：advance_time/change_relation/grant_item。",
            "assistant",
        ),
    ) as hub:
        # Ensure Player input is gated by KP in the same round
        await sequential_pipeline([warrior, mage, player, kp])

        # Dynamic join
        hub.add(blacksmith)
        await hub.broadcast(
            Msg(
                "Host",
                "铁匠走进酒馆，向你们打招呼。可以根据需要调用工具（例如推进时间15分钟、调整两人的关系、分发道具）。",
                "assistant",
            )
        )
        # After blacksmith joins, keep Player->KP adjacency for gating
        await sequential_pipeline([blacksmith, player, kp, mage, warrior])

        # Print world snapshot so we can see tool effects, if any
        print("[system] world:", WORLD.snapshot())

        await hub.broadcast(Msg("Host", "夜深了，大家准备告辞。", "assistant"))


if __name__ == "__main__":
    banner()
    try:
        asyncio.run(tavern_scene())
    except KeyboardInterrupt:
        sys.exit(130)
