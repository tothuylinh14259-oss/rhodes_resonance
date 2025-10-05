from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Tuple

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
from npc_talk.logging import Event, EventType, LoggingContext, create_logging_context
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


async def run_demo(log_ctx: LoggingContext | None = None) -> None:
    """Run the NPC talk demo (sequential group chat, no GM/adjudication)."""
    log_ctx = log_ctx or create_logging_context()

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

    rel_cfg_raw = char_cfg.get("relations") if isinstance(char_cfg, dict) else {}

    def _relation_category(score: int) -> str:
        if score >= 60:
            return "挚友"
        if score >= 40:
            return "亲密同伴"
        if score >= 10:
            return "盟友"
        if score <= -60:
            return "死敌"
        if score <= -40:
            return "仇视"
        if score <= -10:
            return "敌对"
        return "中立"

    def _relation_brief(name: str) -> str:
        if not isinstance(rel_cfg_raw, dict):
            return ""
        mapping = rel_cfg_raw.get(str(name))
        if not isinstance(mapping, dict) or not mapping:
            return ""
        entries: List[str] = []
        for dst, raw in mapping.items():
            if str(dst) == str(name):
                continue
            try:
                score = int(raw)
            except Exception:
                continue
            label = _relation_category(score)
            entries.append(f"{dst}:{score:+d}（{label}）")
        return "；".join(entries)

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
            appearance = entry.get("appearance")
            quotes = entry.get("quotes")
            agent = make_kimi_npc(
                name,
                persona,
                model_cfg,
                prompt_template=npc_prompt_tpl,
                allowed_names=allowed_names_str,
                appearance=appearance,
                quotes=quotes,
                relation_brief=_relation_brief(name),
            )
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
    rel_cfg = rel_cfg_raw or {}
    if isinstance(rel_cfg, dict):
        for src, mapping in rel_cfg.items():
            if not isinstance(mapping, dict):
                continue
            for dst, val in mapping.items():
                try:
                    score = max(-100, min(100, int(val)))
                except Exception:
                    continue
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

    bus = log_ctx.bus if log_ctx else None
    current_round = 0

    def _emit(
        event_type: EventType,
        *,
        actor: Optional[str] = None,
        phase: Optional[str] = None,
        turn: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not bus:
            return
        payload = dict(data or {})
        event = Event(
            event_type=event_type,
            actor=actor,
            phase=phase,
            turn=turn if turn is not None else (current_round or None),
            data=payload,
        )
        bus.publish(event)

    async def _bcast(hub: MsgHub, msg: Msg, *, phase: Optional[str] = None):
        await hub.broadcast(msg)
        text = _safe_text(msg)
        _emit(
            EventType.NARRATIVE,
            actor=msg.name,
            phase=phase,
            data={"text": text, "role": getattr(msg, "role", None)},
        )

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
            phase = f"tool:{tool_name}"
            func = TOOL_DISPATCH.get(tool_name)
            if not func:
                _emit(
                    EventType.ERROR,
                    actor=origin.name,
                    phase=phase,
                    data={
                        "message": f"未知工具调用 {tool_name}",
                        "tool": tool_name,
                        "params": params,
                        "error_type": "tool_not_found",
                    },
                )
                continue
            _emit(
                EventType.TOOL_CALL,
                actor=origin.name,
                phase=phase,
                data={"tool": tool_name, "params": params},
            )
            try:
                resp = func(**params)
            except TypeError as exc:
                _emit(
                    EventType.ERROR,
                    actor=origin.name,
                    phase=phase,
                    data={
                        "message": str(exc),
                        "tool": tool_name,
                        "params": params,
                        "error_type": "invalid_parameters",
                    },
                )
                continue
            except Exception as exc:  # pylint: disable=broad-except
                _emit(
                    EventType.ERROR,
                    actor=origin.name,
                    phase=phase,
                    data={
                        "message": str(exc),
                        "tool": tool_name,
                        "params": params,
                        "error_type": exc.__class__.__name__,
                    },
                )
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
            _emit(
                EventType.TOOL_RESULT,
                actor=origin.name,
                phase=phase,
                data={"tool": tool_name, "metadata": meta, "text": lines},
            )
            if not lines:
                continue
            tool_msg = Msg(
                name=f"{origin.name}[tool]",
                content="\n".join(line for line in lines if line),
                role="assistant",
            )
            await _bcast(hub, tool_msg, phase=phase)

    async with MsgHub(
        participants=list(participants_order),
        announcement=Msg(
            "Host",
            "旧城区·北侧仓棚。铁梁回声震耳，每名战斗者都盯紧了自己的对手——退路已绝，只能分出胜负！",
            "assistant",
        ),
    ) as hub:
        await sequential_pipeline(npcs_list)
        _emit(EventType.STATE_UPDATE, phase="initial", data={"state": WORLD.snapshot()})
        round_idx = 1
        MAX_ROUNDS = 3
        while round_idx <= MAX_ROUNDS:
            current_round = round_idx
            await _bcast(
                hub,
                Msg("Host", f"第{round_idx}回合：小队行动", "assistant"),
                phase="round-start",
            )
            try:
                turn = get_turn()
                meta = turn.metadata or {}
                rnd = int(meta.get("round") or round_idx)
                current_round = rnd
                actor = meta.get("actor")
                state = meta.get("state") or {}
                mv = state.get("move_left")
                a_used = state.get("action_used")
                b_used = state.get("bonus_used")
                r_avail = state.get("reaction_available")
                _emit(
                    EventType.TURN_START,
                    actor=actor,
                    turn=rnd,
                    phase="turn-state",
                    data={
                        "round": rnd,
                        "turn_index": meta.get("turn_idx"),
                        "order": meta.get("order"),
                        "move_left": mv,
                        "action_used": a_used,
                        "bonus_used": b_used,
                        "reaction_available": r_avail,
                    },
                )
            except Exception as exc:
                _emit(
                    EventType.ERROR,
                    phase="turn-state",
                    data={
                        "message": f"获取回合信息失败: {exc}",
                        "error_type": "turn_snapshot",
                    },
                )

            snapshot = WORLD.snapshot()
            _emit(EventType.STATE_UPDATE, phase="world", turn=current_round, data={"state": snapshot})
            await _bcast(
                hub,
                Msg("Host", _world_summary_text(snapshot), "assistant"),
                phase="world-summary",
            )

            # Each NPC acts once
            for agent in npcs_list:
                out = await agent(None)
                await _bcast(
                    hub,
                    out,
                    phase=f"npc:{getattr(agent, 'name', agent.__class__.__name__)}",
                )
                await _handle_tool_calls(out, hub)

            _emit(EventType.TURN_END, phase="round", turn=current_round, data={"round": current_round})
            round_idx += 1

        final_snapshot = WORLD.snapshot()
        _emit(EventType.STATE_UPDATE, phase="final", data={"state": final_snapshot})
        await _bcast(
            hub,
            Msg("Host", "自动演算结束。", "assistant"),
            phase="system",
        )


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
        "关系：参见系统提示，避免违背己方立场",
        ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
        ("坐标：" + "; ".join(pos_lines)) if pos_lines else "坐标：未记录",
        ("角色：" + "; ".join(char_lines)) if char_lines else "角色：未登记",
    ]
    return "\n".join(lines)
