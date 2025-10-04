from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Tuple

from agentscope.agent import AgentBase, ReActAgent  # type: ignore
from agentscope.message import Msg  # type: ignore
from agentscope.pipeline import MsgHub, sequential_pipeline  # type: ignore

from npc_talk.config import (
    load_characters,
    load_feature_flags,
    load_model_config,
    load_narration_policy,
    load_prompts,
)
from npc_talk.agents.factory import make_kimi_npc, TOOL_DISPATCH
from npc_talk.world.tools import (
    WORLD,
    add_objective,
    attack_roll_dnd,
    block_objective,
    change_relation,
    complete_objective,
    describe_world,
    get_position,
    get_stat_block,
    get_turn,
    grant_item,
    move_towards,
    register_hidden_enemy,
    roll_dice,
    saving_throw_dnd,
    schedule_event,
    set_dnd_character,
    set_objective_position,
    set_position,
    set_relation,
    set_scene,
    skill_check,
    skill_check_dnd,
)


_DEFAULT_DOCTOR_PERSONA = (
    "角色：罗德岛‘博士’，战术协调与决策核心。\n"
    "背景：在凯尔希与阿米娅的协助下进行战略研判，偏好以信息整合与资源调配达成目标。\n"
    "说话风格：简短、理性、任务导向；避免夸饰与情绪化表达。\n"
    "边界：不自称超自然或超现实身份；不越权知晓未公开的机密情报。\n"
)


def _join_lines(tpl):
    if isinstance(tpl, list):
        try:
            return "\n".join(str(x) for x in tpl)
        except Exception:
            return "\n".join(tpl)
    return tpl


async def run_demo(logger=None) -> None:
    """Run the NPC talk demo (sequential group chat, no GM/adjudication)."""
    # Load configs (all optional and resilient)
    prompts = load_prompts()
    model_cfg = load_model_config()
    narr_policy = load_narration_policy()
    feature_flags = load_feature_flags()

    doctor_persona = prompts.get("player_persona") or _DEFAULT_DOCTOR_PERSONA
    npc_prompt_tpl = _join_lines(prompts.get("npc_prompt_template"))
    name_map = prompts.get("name_map") or {}

    # Build actors from characters.json or fallback
    char_cfg = load_characters()
    npcs_list: List[ReActAgent] = []
    participants_order: List[AgentBase] = []
    actor_entries: Dict[str, dict] = {}
    try:
        actor_entries = {
            str(k): v
            for k, v in char_cfg.items()
            if isinstance(v, dict)
            and str(k) not in {"relations", "objective_positions", "participants"}
        }
    except Exception:
        actor_entries = {}
    order = char_cfg.get("participants") or []
    allowed_names = [str(name) for name in order] if isinstance(order, list) else []
    all_actor_names = list({*allowed_names, *actor_entries.keys()})
    allowed_names_str = ", ".join(all_actor_names) if all_actor_names else "Amiya, Doctor"

    if allowed_names:
        for name in allowed_names:
            entry = (char_cfg.get(name) or {}) if isinstance(char_cfg, dict) else {}
            # Stat block & position
            dnd = entry.get("dnd") or {}
            if dnd:
                try:
                    set_dnd_character(
                        name=name,
                        level=int(dnd.get("level", 1)),
                        ac=int(dnd.get("ac", 10)),
                        abilities=dnd.get("abilities") or {},
                        max_hp=int(dnd.get("max_hp", 8)),
                        proficient_skills=dnd.get("proficient_skills") or [],
                        proficient_saves=dnd.get("proficient_saves") or [],
                        move_speed=int(dnd.get("move_speed", 6)),
                    )
                except Exception:
                    pass
            pos = entry.get("position")
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                try:
                    set_position(name, int(pos[0]), int(pos[1]))
                except Exception:
                    pass
            persona = entry.get("persona") or (doctor_persona if name == "Doctor" else "一个简短的人设描述")
            agent = make_kimi_npc(name, persona, model_cfg, prompt_template=npc_prompt_tpl, allowed_names=allowed_names_str)
            npcs_list.append(agent)
            participants_order.append(agent)
        # preload non-participant actors (e.g., enemies) into world sheets
        for name, entry in actor_entries.items():
            if name in allowed_names:
                continue
            dnd = entry.get("dnd") or {}
            if dnd:
                try:
                    set_dnd_character(
                        name=name,
                        level=int(dnd.get("level", 1)),
                        ac=int(dnd.get("ac", 10)),
                        abilities=dnd.get("abilities") or {},
                        max_hp=int(dnd.get("max_hp", 8)),
                        proficient_skills=dnd.get("proficient_skills") or [],
                        proficient_saves=dnd.get("proficient_saves") or [],
                        move_speed=int(dnd.get("move_speed", 6)),
                    )
                except Exception:
                    pass
            pos = entry.get("position")
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                try:
                    set_position(name, int(pos[0]), int(pos[1]))
                except Exception:
                    pass
    else:
        # Fallback to Amiya + Doctor
        allowed_names_str = ", ".join(["Amiya", "Doctor"])  # fallback visible names
        from npc_talk.agents.factory import make_kimi_npc as _mk

        amiya = _mk(
            "Amiya",
            "罗德岛公开领导人阿米娅。温柔而坚定，理性克制，关切同伴；擅长源石技艺（术师），发言简洁不夸张。",
            model_cfg,
            prompt_template=npc_prompt_tpl,
            allowed_names=allowed_names_str,
        )
        doctor = _mk(
            "Doctor",
            doctor_persona,
            model_cfg,
            prompt_template=npc_prompt_tpl,
            allowed_names=allowed_names_str,
        )
        npcs_list = [amiya, doctor]
        participants_order = [amiya, doctor]
        try:
            set_position("Amiya", 0, 1)
            set_position("Doctor", 0, 0)
        except Exception:
            pass

    # Initialize relations from config
    rel_cfg = char_cfg.get("relations") or {}
    seen_pairs: set[tuple[str, str]] = set()
    if isinstance(rel_cfg, dict):
        for src, mapping in rel_cfg.items():
            if not isinstance(mapping, dict):
                continue
            for dst, val in mapping.items():
                try:
                    score = max(-100, min(100, int(val)))
                except Exception:
                    continue
                pair = tuple(sorted([str(src), str(dst)]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                try:
                    set_relation(str(src), str(dst), score, reason="配置设定")
                except Exception:
                    pass

    # Ensure Doctor exists
    if "Doctor" not in WORLD.characters:
        set_dnd_character(
            name="Doctor",
            level=1,
            ac=14,
            abilities={"STR": 12, "DEX": 16, "CON": 14, "INT": 10, "WIS": 14, "CHA": 10},
            max_hp=12,
            proficient_skills=["athletics", "insight", "medicine"],
            proficient_saves=["STR", "DEX"],
            move_speed=6,
        )

    # Scene setup
    set_scene("旧城区·北侧仓棚", ["冲突终结"])  # single victory condition objective

    # Logging helpers
    def _log_tag(tag: str, text: str):
        try:
            if logger:
                logger.info(f"[{tag}] {text}")
        except Exception:
            pass

    async def _bcast(hub: MsgHub, msg: Msg):
        await hub.broadcast(msg)
        try:
            if logger:
                try:
                    text = msg.get_text_content()
                except Exception:
                    text = None
                if text is None:
                    c = getattr(msg, "content", "")
                    text = c if isinstance(c, str) else str(c)
                logger.info(f"{msg.name}: {text}")
        except Exception:
            pass

    TOOL_CALL_PATTERN = re.compile(r"CALL_TOOL\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<body>.*)\)")

    def _safe_text(msg: Msg) -> str:
        try:
            text = msg.get_text_content()
        except Exception:
            text = None
        if text is not None:
            return str(text)
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            lines = []
            for blk in content:
                if hasattr(blk, "text"):
                    lines.append(str(getattr(blk, "text", "")))
                elif isinstance(blk, dict):
                    lines.append(str(blk.get("text", "")))
            return "\n".join(line for line in lines if line)
        return str(content)

    def _parse_tool_calls(text: str) -> List[Tuple[str, dict]]:
        calls: List[Tuple[str, dict]] = []
        if not text:
            return calls
        idx = 0
        while True:
            m = TOOL_CALL_PATTERN.search(text, idx)
            if not m:
                break
            name = m.group("name")
            body = m.group("body").strip()
            start = 0
            brace = 0
            json_body = None
            for i, ch in enumerate(body):
                if ch == "{" and brace == 0:
                    start = i
                    brace = 1
                    continue
                if ch == "{" and brace > 0:
                    brace += 1
                elif ch == "}" and brace > 0:
                    brace -= 1
                    if brace == 0:
                        json_body = body[start : i + 1]
                        break
            params = {}
            if json_body:
                try:
                    params = json.loads(json_body)
                except Exception:
                    params = {}
            calls.append((name, params))
            idx = m.end()
        return calls

    async def _handle_tool_calls(origin: Msg, hub: MsgHub):
        text = _safe_text(origin)
        tool_calls = _parse_tool_calls(text)
        if not tool_calls:
            return
        for tool_name, params in tool_calls:
            func = TOOL_DISPATCH.get(tool_name)
            if not func:
                _log_tag("TOOL", f"未知工具调用：{tool_name} params={params}")
                continue
            try:
                resp = func(**params)
            except TypeError as exc:
                _log_tag("TOOL", f"工具参数错误 {tool_name}: {exc}")
                continue
            except Exception as exc:  # pylint: disable=broad-except
                _log_tag("TOOL", f"工具执行异常 {tool_name}: {exc}")
                continue
            text_blocks = getattr(resp, "content", None)
            lines: List[str] = []
            if isinstance(text_blocks, list):
                for blk in text_blocks:
                    if hasattr(blk, "text"):
                        lines.append(str(getattr(blk, "text", "")))
                    elif isinstance(blk, dict):
                        lines.append(str(blk.get("text", "")))
                    else:
                        lines.append(str(blk))
            meta = getattr(resp, "metadata", None)
            if not lines and meta:
                try:
                    lines.append(json.dumps(meta, ensure_ascii=False))
                except Exception:
                    lines.append(str(meta))
            if not lines:
                continue
            tool_msg = Msg(
                name=f"{origin.name}[tool]",
                content="\n".join(line for line in lines if line),
                role="assistant",
            )
            await _bcast(hub, tool_msg)

    async with MsgHub(
        participants=list(participants_order),
        announcement=Msg(
            "Host",
            "旧城区·北侧仓棚。铁梁回声震耳，每名战斗者都盯紧了自己的对手——退路已绝，只能分出胜负！",
            "assistant",
        ),
    ) as hub:
        await sequential_pipeline(npcs_list)
        round_idx = 1
        MAX_ROUNDS = 3
        while round_idx <= MAX_ROUNDS:
            await _bcast(hub, Msg("Host", f"第{round_idx}回合：小队行动", "assistant"))
            try:
                turn = get_turn()
                meta = turn.metadata or {}
                actor = meta.get("actor")
                rnd = meta.get("round")
                state = meta.get("state") or {}
                mv = state.get("move_left")
                a_used = state.get("action_used")
                b_used = state.get("bonus_used")
                r_avail = state.get("reaction_available")
                _log_tag("TURN", f"R{rnd} actor={actor} move_left={mv} action_used={a_used} bonus_used={b_used} reaction_avail={r_avail}")
            except Exception:
                pass

            await _bcast(hub, Msg("Host", _world_summary_text(WORLD.snapshot()), "assistant"))

            # Each NPC acts once
            for a in npcs_list:
                out = await a(None)
                await _bcast(hub, out)
                await _handle_tool_calls(out, hub)

            print("[system] world:", WORLD.snapshot())
            round_idx += 1

        await _bcast(hub, Msg("Host", "自动演算结束。", "assistant"))


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
    positions = snap.get("positions", {}) or {}
    pos_lines = []
    try:
        for nm, coord in positions.items():
            if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                pos_lines.append(f"{nm}({coord[0]}, {coord[1]})")
    except Exception:
        pos_lines = []
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
        ("坐标：" + "; ".join(pos_lines)) if pos_lines else "坐标：未记录",
        ("角色：" + "; ".join(char_lines)) if char_lines else "角色：未登记",
    ]
    return "\n".join(lines)
