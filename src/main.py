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

# --- Player persona (used by KP to rewrite tone/intent) ---
PLAYER_PERSONA = (
    "角色：游侠罗文（Rowan），27岁，北境林地斥候。\n"
    "背景：曾为边境哨所侦察兵，厌恶无谓流血，偏爱智取与谈判。\n"
    "目标（短期）：打探‘龙骸’传闻的真假，寻找失踪的向导。\n"
    "目标（中期）：为故乡与铁匠铺清偿旧债。\n"
    "说话风格：简短、克制，偶有冷幽默；避免夸饰。\n"
    "禁区：不自称神/王/贵族；不知未来与他人隐私。\n"
)
from world.tools import WORLD, advance_time, change_relation, grant_item, describe_world


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
        "- 若需要了解环境信息，优先调用 describe_world()，不要凭空臆测世界状态。\n"
        "- 工具调用完成后，再用一句话向对话对象说明处理结果。\n"
        "- 若本回合没有新的有效信息、或沉默更合适，请直接输出：[skip]（仅此标记，无其它文字）。"
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
    toolkit.register_tool_function(describe_world)

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
    kp = KPAgent(name="KP", player_persona=PLAYER_PERSONA)
    # Provide KP with a world snapshot provider so it can see the environment context
    kp.set_world_snapshot_provider(lambda: WORLD.snapshot())

    async with MsgHub(
        participants=[warrior, mage, player, kp],
        announcement=Msg(
            "Host",
            "你们在酒馆壁炉旁相识。做个自我介绍。\n提示：玩家可直接发言；如需推进剧情，NPC 可使用工具：advance_time/change_relation/grant_item。",
            "assistant",
        ),
    ) as hub:
        # Opening: warriors/mage introduce
        await sequential_pipeline([warrior, mage])
        # Player <-> KP handshake before others act (isolate from others)
        if await run_player_kp_handshake(hub, player, kp):
            return

        # Dynamic join
        hub.add(blacksmith)
        await hub.broadcast(
            Msg(
                "Host",
                "铁匠走进酒馆，向你们打招呼。可以根据需要调用工具（例如推进时间15分钟、调整两人的关系、分发道具）。",
                "assistant",
            )
        )
        # After blacksmith joins: have blacksmith speak first
        await sequential_pipeline([blacksmith])
        # Main loop: continue rounds until player quits
        round_idx = 1
        while True:
            await hub.broadcast(Msg("Host", f"第{round_idx}回合：玩家行动（输入 /quit 退出）", "assistant"))
            # Host broadcasts a brief environment summary each round
            await hub.broadcast(Msg("Host", _world_summary_text(WORLD.snapshot()), "assistant"))
            if await run_player_kp_handshake(hub, player, kp):
                await hub.broadcast(Msg("Host", "本次冒险暂告一段落。", "assistant"))
                break
            # Others react this round
            await run_npc_round(hub, [mage, warrior, blacksmith])
            # Snapshot for visibility
            print("[system] world:", WORLD.snapshot())
            round_idx += 1


async def run_player_kp_handshake(hub: MsgHub, player: PlayerAgent, kp: KPAgent, max_steps: int = 8) -> bool:
    """Run an isolated Player<->KP handshake:
    - Temporarily disable auto broadcast so raw Player输入与KP提案不会影响其他NPC；
    - 仅在确认“是”后，将最终改写后的 Player 发言广播给所有参与者。
    """
    # Temporarily disable auto broadcast so other agents won't observe raw inputs
    hub.set_auto_broadcast(False)
    try:
        steps = 0
        while steps < max_steps:
            # 1) Player speaks (no auto broadcast)
            out_p = await player(None)
            # Handle /quit fast path
            if hasattr(player, "wants_exit") and callable(getattr(player, "wants_exit")):
                try:
                    if player.wants_exit():
                        await hub.broadcast(out_p)
                        return True
                except Exception:
                    pass
            # Note: /skip 不再直接跳过，而是交由 KP 改写为世界化的被动描写并走确认流程
            # Deliver to KP only
            await kp.observe(out_p)

            # 2) KP responds (either clarification or confirmation proposal or final Player msg)
            out_kp = await kp(None)

            # If KP returned a finalized Player message, broadcast it to all and stop
            if getattr(out_kp, "name", "") == "Player" and getattr(out_kp, "role", "") == "user":
                await hub.broadcast(out_kp)
                return False

            # Otherwise, deliver KP's assistant reply back to Player only (no broadcast)
            await player.observe(out_kp)

            steps += 1
    finally:
        # Re-enable auto broadcast for subsequent turns
        hub.set_auto_broadcast(True)
    return False


async def run_npc_round(hub: MsgHub, agents: list[ReActAgent]):
    """Run a non-blocking round for NPCs where each NPC may choose to skip.
    - Auto-broadcast is disabled; we only broadcast non-[skip] outputs.
    - Tool effects still execute (world updates) and messages are printed to console.
    """
    hub.set_auto_broadcast(False)
    try:
        for a in agents:
            out = await a(None)
            # Extract text to detect skip
            text = None
            try:
                text = out.get_text_content()
            except Exception:
                text = None
            norm = (text or "").strip()
            if norm == "[skip]" or norm == "(沉默)" or norm == "（沉默）" or norm == "":
                continue  # skip broadcasting
            await hub.broadcast(out)
    finally:
        hub.set_auto_broadcast(True)


def _world_summary_text(snap: dict) -> str:
    try:
        t = int(snap.get("time_min", 0))
    except Exception:
        t = 0
    hh, mm = t // 60, t % 60
    weather = snap.get("weather", "unknown")
    rels = snap.get("relations", {}) or {}
    try:
        rel_lines = [f"{k}:{v}" for k, v in rels.items()]
    except Exception:
        rel_lines = []
    inv = snap.get("inventory", {}) or {}
    inv_lines = []
    try:
        for who, bag in inv.items():
            if not bag:
                continue
            inv_lines.append(f"{who}[" + ", ".join(f"{it}:{cnt}" for it, cnt in bag.items()) + "]")
    except Exception:
        pass
    lines = [
        f"环境概要：时间 {hh:02d}:{mm:02d}，天气 {weather}",
        ("关系：" + "; ".join(rel_lines)) if rel_lines else "关系：无变动",
        ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    banner()
    try:
        asyncio.run(tavern_scene())
    except KeyboardInterrupt:
        sys.exit(130)
