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
from world.tools import (
    WORLD,
    advance_time,
    change_relation,
    grant_item,
    describe_world,
    set_character,
    get_character,
    damage,
    heal,
    roll_dice,
    skill_check,
    resolve_melee_attack,
    # D&D-like
    set_dnd_character,
    get_stat_block,
    skill_check_dnd,
    saving_throw_dnd,
    attack_roll_dnd,
)


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
        "- 有不确定结果/对抗判定时，使用 attack_roll_dnd()/skill_check_dnd()/saving_throw_dnd()。不要直接宣布结果。\n"
        "- 工具调用完成后，再用一句话向对话对象说明处理结果。\n"
        "- 若本回合选择不推进剧情，请输出一条“维持当前姿态/动作/观察”的简短描写（1句），不要输出 [skip]，也不要调用工具。"
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
    # Character/stat & dice tools
    toolkit.register_tool_function(get_character)
    toolkit.register_tool_function(damage)
    toolkit.register_tool_function(heal)
    toolkit.register_tool_function(roll_dice)
    toolkit.register_tool_function(skill_check)
    # D&D-like tools
    toolkit.register_tool_function(set_dnd_character)
    toolkit.register_tool_function(get_stat_block)
    toolkit.register_tool_function(skill_check_dnd)
    toolkit.register_tool_function(saving_throw_dnd)
    toolkit.register_tool_function(attack_roll_dnd)

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

    # Initialize D&D-like stat blocks
    set_dnd_character(
        name="Player",
        level=1,
        ac=14,
        abilities={"STR": 12, "DEX": 16, "CON": 14, "INT": 10, "WIS": 14, "CHA": 10},
        max_hp=12,
        proficient_skills=["perception", "stealth", "survival", "athletics"],
        proficient_saves=["STR", "DEX"],
    )
    set_dnd_character(
        name="Warrior",
        level=1,
        ac=16,
        abilities={"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 12},
        max_hp=14,
        proficient_skills=["athletics", "intimidation"],
        proficient_saves=["STR", "CON"],
    )
    set_dnd_character(
        name="Mage",
        level=1,
        ac=12,
        abilities={"STR": 8, "DEX": 14, "CON": 12, "INT": 16, "WIS": 12, "CHA": 10},
        max_hp=8,
        proficient_skills=["arcana", "history", "investigation"],
        proficient_saves=["INT", "WIS"],
    )
    set_dnd_character(
        name="Blacksmith",
        level=1,
        ac=12,
        abilities={"STR": 14, "DEX": 10, "CON": 14, "INT": 10, "WIS": 12, "CHA": 10},
        max_hp=12,
        proficient_skills=["athletics", "history"],
        proficient_saves=["CON", "WIS"],
    )

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
            # /skip: 改为“直接改写并落地”，不再二次确认
            if hasattr(player, "wants_skip") and callable(getattr(player, "wants_skip")):
                try:
                    if player.wants_skip():
                        # 让 KP 直接改写为被动姿态，并返回最终 Player 消息
                        if hasattr(kp, "rewrite_skip_immediately"):
                            final_msg = await kp.rewrite_skip_immediately()
                            await hub.broadcast(final_msg)
                            return False
                except Exception:
                    pass
            # Deliver to KP only
            await kp.observe(out_p)

            # 2) KP responds (either clarification or confirmation proposal or final Player msg)
            out_kp = await kp(None)

            # If KP returned a finalized Player message, broadcast it to all and stop
            if getattr(out_kp, "name", "") == "Player" and getattr(out_kp, "role", "") == "user":
                await hub.broadcast(out_kp)
                # Auto-resolve melee if player's action indicates a melee attack
                await _maybe_auto_resolve_player_melee(hub, out_kp)
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
    - Auto-broadcast is disabled; we broadcast每个NPC的一句输出。\n
    - 工具效果照常执行（更新世界），并打印到控制台。\n
    """
    hub.set_auto_broadcast(False)
    try:
        for a in agents:
            out = await a(None)
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
    # Characters
    chars = snap.get("characters", {}) or {}
    char_lines = []
    try:
        for nm, st in chars.items():
            hp = st.get("hp"); max_hp = st.get("max_hp")
            if hp is not None and max_hp is not None:
                char_lines.append(f"{nm}(HP {hp}/{max_hp})")
    except Exception:
        pass

    lines = [
        f"环境概要：时间 {hh:02d}:{mm:02d}，天气 {weather}",
        ("关系：" + "; ".join(rel_lines)) if rel_lines else "关系：无变动",
        ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
        ("角色：" + "; ".join(char_lines)) if char_lines else "角色：未登记",
    ]
    return "\n".join(lines)


async def _maybe_auto_resolve_player_melee(hub: MsgHub, player_msg):
    """Heuristic: if player's finalized message looks like a melee attack to
    Mage/Warrior/Blacksmith, run a simple melee resolution and broadcast the
    result as a Host message.
    """
    try:
        text = player_msg.get_text_content() or ""
    except Exception:
        return
    low = text.lower()
    # Attack verbs (CN+EN)
    hit_keywords = [
        "拳", "打", "揍", "砸", "击", "掌", "掴", "踢", "撞", "推", "刺", "砍", "劈",
        "punch", "hit", "strike", "kick",
    ]
    if not any(k in low for k in hit_keywords):
        return
    # Target mapping by keywords
    target_map = {
        "mage": "Mage", "法师": "Mage",
        "warrior": "Warrior", "勇士": "Warrior",
        "blacksmith": "Blacksmith", "铁匠": "Blacksmith",
    }
    target = None
    for k, v in target_map.items():
        if k in text:
            target = v
            break
    if not target:
        return
    # Resolve attack with default DC and damage
    res = resolve_melee_attack(attacker="Player", defender=target, atk_mod=0, dc=12, dmg_expr="1d4")
    # Summarize ToolResponse content to a Host message
    out_lines = []
    for blk in res.content or []:
        if blk.get("type") == "text":
            out_lines.append(blk.get("text", ""))
    if out_lines:
        await hub.broadcast(Msg("Host", "\n".join(out_lines), "assistant"))


if __name__ == "__main__":
    banner()
    try:
        asyncio.run(tavern_scene())
    except KeyboardInterrupt:
        sys.exit(130)
