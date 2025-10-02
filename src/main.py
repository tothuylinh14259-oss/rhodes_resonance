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

from agents.player import PlayerAgent
from agents.kp import KPAgent
from agentscope.agent import AgentBase  # type: ignore

# --- Player persona (used by KP to rewrite tone/intent) ---
PLAYER_PERSONA = (
    "角色：罗德岛‘博士’，战术协调与决策核心。\n"
    "背景：在凯尔希与阿米娅的协助下进行战略研判，偏好以信息整合与资源调配达成目标。\n"
    "说话风格：简短、理性、任务导向；避免夸饰与情绪化表达。\n"
    "边界：不自称超自然或超现实身份；不越权知晓未公开的机密情报。\n"
)
from world.tools import (
    WORLD,
    advance_time,
    change_relation,
    grant_item,
    describe_world,
    set_scene,
    add_objective,
    set_character,
    get_character,
    damage,
    heal,
    roll_dice,
    skill_check,
    # D&D-like
    set_dnd_character,
    get_stat_block,
    skill_check_dnd,
    saving_throw_dnd,
    attack_roll_dnd,
    # Director/event helpers
    schedule_event,
    complete_objective,
    block_objective,
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

    # Intent-only NPCs: Speak 1-2 sentences, then output a JSON intent block for KP to adjudicate.        # Intent-only NPCs: Speak 1-2 sentences, then output a JSON intent block for KP to adjudicate.
    sys_prompt = f"你是游戏中的NPC：{name}。人设：{persona}。\n" + (
        """对话要求：
- 先用简短中文说1-2句对白/想法/微动作，符合人设。
- 然后给出一个 JSON 意图（不要调用任何工具；裁决由KP执行）。
- 若需要了解环境信息，可调用 describe_world()；除此之外不要调用其他工具。
意图JSON格式（仅保留需要的字段）：
{
  "intent": "attack|talk|investigate|move|assist|use_item|skill_check|wait",
  "target": "目标名称",
  "skill": "perception|medicine|...",
  "ability": "STR|DEX|CON|INT|WIS|CHA",
  "proficient": true,
  "dc_hint": 12,
  "damage_expr": "1d4+STR",
  "time_cost": 1,
  "notes": "一句话说明意图"
}
输出示例：
阿米娅看向博士，压低声音：‘要先确认辐射数据。’
```json
{\"intent\":\"skill_check\",\"target\":\"Amiya\",\"skill\":\"investigation\",\"dc_hint\":12,\"notes\":\"核对监测表\"}
```
"""
    )

    model = OpenAIChatModel

    model = OpenAIChatModel(
        model_name=model_name,
        api_key=api_key,
        stream=True,  # enable streaming output
        client_args={"base_url": base_url},
        generate_kwargs={"temperature": 0.7},
    )

    # Equip only describe_world; all other tools are executed by KP adjudicator.
    toolkit = Toolkit()
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
    amiya = make_kimi_npc("Amiya", "罗德岛公开领导人阿米娅。温柔而坚定，理性克制，关切同伴；擅长源石技艺（术师），发言简洁不夸张。")
    kaltsit = make_kimi_npc("Kaltsit", "罗德岛医疗部门负责人凯尔希。冷静苛刻、直言不讳，注重风险控制与证据；医疗/生物技术专家。")
    player = PlayerAgent(name="Doctor", prompt="博士> ")
    kp = KPAgent(name="KP", player_persona=PLAYER_PERSONA, player_name="Doctor")
    # Provide KP with a world snapshot provider so it can see the environment context
    kp.set_world_snapshot_provider(lambda: WORLD.snapshot())

    # Initialize scene & objectives
    set_scene("罗德岛·会议室", [
        "确认切尔诺伯格核心城残骸的辐射监测数据",
        "在伦蒂尼姆行动前完成风险评估",
    ])

    # Initialize D&D-like stat blocks
    set_dnd_character(
        name="Doctor",
        level=1,
        ac=14,
        abilities={"STR": 12, "DEX": 16, "CON": 14, "INT": 10, "WIS": 14, "CHA": 10},
        max_hp=12,
        proficient_skills=["perception", "stealth", "survival", "athletics"],
        proficient_saves=["STR", "DEX"],
    )
    set_dnd_character(
        name="Amiya",
        level=1,
        ac=12,
        abilities={"STR": 8, "DEX": 14, "CON": 12, "INT": 16, "WIS": 12, "CHA": 12},
        max_hp=10,
        proficient_skills=["arcana", "history", "persuasion"],
        proficient_saves=["INT", "WIS"],
    )
    set_dnd_character(
        name="Kaltsit",
        level=1,
        ac=14,
        abilities={"STR": 10, "DEX": 12, "CON": 14, "INT": 16, "WIS": 14, "CHA": 10},
        max_hp=14,
        proficient_skills=["medicine", "investigation", "history"],
        proficient_saves=["INT", "WIS"],
    )

    # Seed timed events so the world keeps moving even if players skip.
    now = WORLD.time_min
    schedule_event(
        name="监测科上报初步辐射值",
        at_min=now + 5,
        note="伦蒂尼姆方向辐射读数偏高，需二次确认",
        effects=[{"kind": "add_objective", "name": "确认辐射值并拟定防护预案"}],
    )
    schedule_event(
        name="运输路线暴露风险上升",
        at_min=now + 15,
        note="敌对势力侦查迹象明确，需调整路径",
        effects=[{"kind": "add_objective", "name": "调整运送路线以降低暴露"}],
    )
    schedule_event(
        name="感染者急性发作个案",
        at_min=now + 30,
        note="医疗部报告一例急性发作，需隔离与紧急用药",
        effects=[{"kind": "add_objective", "name": "处置急性发作并稳定队伍"}],
    )


    # Prepare a mutable NPC list; enemies may join later during encounters
    npcs_list: list[ReActAgent] = [kaltsit, amiya]

    # Simple encounter trigger: after a short in-world delay, enemies闯入
    encounter_trigger_min = WORLD.time_min + 7

    async with MsgHub(
        participants=[amiya, kaltsit, player, kp],
        announcement=Msg(
            "Host",
            "罗德岛·会议室。各位做好简短自我介绍，并对当前事务做快速同步。\n提示：玩家可直接发言；如需推进剧情，NPC 可使用工具/检定：describe_world/skill_check_dnd/attack_roll_dnd。",
            "assistant",
        ),
    ) as hub:
        # Opening: Amiya/Kaltsit introduce
        await sequential_pipeline([amiya, kaltsit])
        # Note: 不在开场阶段进行 Player↔KP 握手；只在回合内进行，
        # 避免“确认后立刻又到玩家回合”的体验跳变。

        # Main loop: continue rounds until player quits
        round_idx = 1
        while True:
            # 主持人信息也打印到控制台，避免只进入消息总线而不显示
            _hdr = Msg("Host", f"第{round_idx}回合：玩家行动（输入 /quit 退出）", "assistant")
            await hub.broadcast(_hdr)
            try:
                await kp.print(_hdr)
            except Exception:
                pass
            _sum = Msg("Host", _world_summary_text(WORLD.snapshot()), "assistant")
            await hub.broadcast(_sum)
            try:
                await kp.print(_sum)
            except Exception:
                pass
            should_end, player_final = await run_player_kp_handshake(hub, player, kp)
            if should_end:
                await hub.broadcast(Msg("Host", "本次冒险暂告一段落。", "assistant"))
                break
            # Immediately adjudicate confirmed player action
            if player_final is not None:
                judge_msgs = await kp.adjudicate([player_final])
                for m in judge_msgs:
                    await hub.broadcast(m)
                    try:
                        await kp.print(m)
                    except Exception:
                        pass
            # Maybe trigger an encounter: enemies break in and start combat
            if WORLD.time_min >= encounter_trigger_min:
                await _run_simple_encounter(hub, kp, player, npcs_list)
                # Reset trigger far in the future so it doesn't fire again
                encounter_trigger_min = WORLD.time_min + 9999

            # NPCs (non-encounter) act stepwise with immediate adjudication
            await run_npc_round_stepwise(hub, kp, npcs_list)
            print("[system] world:", WORLD.snapshot())
            round_idx += 1


async def run_player_kp_handshake(hub: MsgHub, player: PlayerAgent, kp: KPAgent, max_steps: int = 8) -> tuple[bool, Msg | None]:
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
                        return True, out_p
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
                            return False, final_msg
                except Exception:
                    pass
            # Deliver to KP only
            await kp.observe(out_p)

            # 2) KP responds (either clarification or confirmation proposal or final Player msg)
            out_kp = await kp(None)

            # If KP returned a finalized Player message, broadcast it to all and stop
            if getattr(out_kp, "name", "") == getattr(player, "name", "Player") and getattr(out_kp, "role", "") == "user":
                await hub.broadcast(out_kp)
                return False, out_kp

            # Otherwise, deliver KP's assistant reply back to Player only (no broadcast)
            await player.observe(out_kp)

            steps += 1
    finally:
        # Re-enable auto broadcast for subsequent turns
        hub.set_auto_broadcast(True)
    return False, None


async def run_npc_round_stepwise(hub: MsgHub, kp: KPAgent, agents: list[ReActAgent]) -> None:
    """NPCs act one by one; after each action, KP adjudicates immediately and
    broadcasts results (including time advancement and events)."""
    hub.set_auto_broadcast(False)
    try:
        for a in agents:
            out = await a(None)
            await hub.broadcast(out)
            judge_msgs = await kp.adjudicate([out])
            for m in judge_msgs:
                await hub.broadcast(m)
                try:
                    await kp.print(m)
                except Exception:
                    pass
    finally:
        hub.set_auto_broadcast(True)


def _world_summary_text(snap: dict) -> str:
    try:
        t = int(snap.get("time_min", 0))
    except Exception:
        t = 0
    hh, mm = t // 60, t % 60
    weather = snap.get("weather", "unknown")
    location = snap.get("location", "未知")
    objectives = snap.get("objectives", []) or []
    obj_status = snap.get("objective_status", {}) or {}
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
        f"环境概要：地点 {location}；时间 {hh:02d}:{mm:02d}；天气 {weather}",
        ("目标：" + "; ".join((f"{str(o)}({obj_status.get(str(o))})" if obj_status.get(str(o)) else str(o)) for o in objectives)) if objectives else "目标：无",
        ("关系：" + "; ".join(rel_lines)) if rel_lines else "关系：无变动",
        ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
        ("角色：" + "; ".join(char_lines)) if char_lines else "角色：未登记",
    ]
    return "\n".join(lines)


# Obsolete auto-resolver removed; KP.adjudicate handles results.


# ------------------- Encounter Helpers -------------------
async def _run_simple_encounter(hub: MsgHub, kp: KPAgent, player: PlayerAgent, friendlies: list[ReActAgent]) -> None:
    """Lightweight encounter mode with initiative order.
    - Spawns a small enemy group
    - Rolls initiative (d20+DEX mod) for all combatants
    - Runs a turn loop until enemies are defeated or player exits
    """
    # 1) Spawn enemies (stat blocks + scripted agents)
    enemies = _spawn_enemy_group()
    await hub.broadcast(Msg("Host", "[遭遇] 通道尽头传来脚步与金属碰撞声——两名整合运动突击手破门而入！", "assistant"))
    # 2) Build initiative
    names = ["Doctor", "Amiya", "Kaltsit"] + [e.name for e in enemies]
    order = _roll_initiative(names)
    order_names = [n for n, _ in order]
    await hub.broadcast(Msg("Host", "[先攻] 顺序：" + ", ".join(order_names), "assistant"))

    # Build name->agent mapping; Player handled specially by handshake
    name_to_agent: dict[str, AgentBase] = {
        "Doctor": player,
        "Amiya": next(a for a in friendlies if getattr(a, "name", "") == "Amiya"),
        "Kaltsit": next(a for a in friendlies if getattr(a, "name", "") == "Kaltsit"),
    }
    for e in enemies:
        name_to_agent[e.name] = e

    # 3) Turn loop
    i = 0
    max_turns = 200  # hard stop safety
    while i < max_turns:
        # Remove defeated enemies from order
        order = [(n, ini) for (n, ini) in order if not _is_defeated_enemy(n)]
        if not any(n.startswith("RI_Raider") for n, _ in order):
            await hub.broadcast(Msg("Host", "[遭遇结束] 敌人被击退/制伏。", "assistant"))
            break
        actor = order[i % len(order)][0]
        # Player's turn via handshake
        if actor == "Doctor":
            should_end, player_final = await run_player_kp_handshake(hub, player, kp)
            if should_end:
                await hub.broadcast(Msg("Host", "本次冒险暂告一段落。", "assistant"))
                return
            if player_final is not None:
                judge_msgs = await kp.adjudicate([player_final])
                for m in judge_msgs:
                    await hub.broadcast(m)
                    try:
                        await kp.print(m)
                    except Exception:
                        pass
        else:
            # NPC/enemy action: single step, immediate adjudication
            agent = name_to_agent.get(actor)
            if agent is not None:
                # Provide a brief world snapshot hint to the agent
                try:
                    await agent.observe(Msg("Host", _world_summary_text(WORLD.snapshot()), "assistant"))
                except Exception:
                    pass
                out = await agent(None)
                await hub.broadcast(out)
                judge_msgs = await kp.adjudicate([out])
                for m in judge_msgs:
                    await hub.broadcast(m)
                    try:
                        await kp.print(m)
                    except Exception:
                        pass
        i += 1


def _roll_initiative(names: list[str]) -> list[tuple[str, int]]:
    from world.tools import WORLD as _W
    import random
    def mod(score: int) -> int:
        return (int(score) - 10) // 2
    rolls: list[tuple[str, int]] = []
    for n in names:
        try:
            dex = int((_W.characters.get(n, {}) or {}).get("abilities", {}).get("DEX", 10))
        except Exception:
            dex = 10
        ini = random.randint(1, 20) + mod(dex)
        rolls.append((n, ini))
    rolls.sort(key=lambda x: x[1], reverse=True)
    return rolls


def _spawn_enemy_group() -> list[AgentBase]:
    """Create a small enemy group with stat blocks and simple scripted agents."""
    # Define two raiders
    set_dnd_character(
        name="RI_Raider1",
        level=1,
        ac=13,
        abilities={"STR": 12, "DEX": 12, "CON": 12, "INT": 8, "WIS": 10, "CHA": 8},
        max_hp=9,
        proficient_skills=["athletics", "perception"],
    )
    set_dnd_character(
        name="RI_Raider2",
        level=1,
        ac=13,
        abilities={"STR": 12, "DEX": 12, "CON": 12, "INT": 8, "WIS": 10, "CHA": 8},
        max_hp=9,
        proficient_skills=["athletics", "perception"],
    )

    class ScriptedEnemy(AgentBase):
        def __init__(self, name: str, target: str = "Doctor") -> None:
            super().__init__()
            self.name = name
            self.target = target
            self._intro_done = False
        async def observe(self, msg):
            # no-op minimal memory
            return None
        async def reply(self, msg=None):
            # First time say one line, then attack
            if not self._intro_done:
                self._intro_done = True
                txt = f"{self.name} 挥起短棍逼近，低喝：‘交出通行证！’"
            else:
                txt = f"{self.name} 侧身压步，狠击{self.target}的肋侧。"
            intent = {
                "intent": "attack",
                "target": self.target,
                "ability": "STR",
                "proficient": False,
                "damage_expr": "1d6+STR",
                "time_cost": 1,
                "notes": "短棍攻击",
            }
            content = txt + "\n```json\n" + __import__("json").dumps(intent, ensure_ascii=False) + "\n```"
            out = Msg(self.name, content, "assistant")
            await self.print(out)
            return out
        async def handle_interrupt(self, *args, **kwargs):
            msg = Msg(self.name, "（敌人被干扰，暂缓动作）", "assistant")
            await self.print(msg)
            return msg

    return [ScriptedEnemy("RI_Raider1"), ScriptedEnemy("RI_Raider2")]


if __name__ == "__main__":
    banner()
    try:
        asyncio.run(tavern_scene())
    except KeyboardInterrupt:
        sys.exit(130)
