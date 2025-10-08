from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Callable, Mapping

from agentscope.agent import AgentBase, ReActAgent  # type: ignore
from agentscope.message import Msg  # type: ignore
from agentscope.pipeline import MsgHub, sequential_pipeline  # type: ignore


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


async def run_demo(
    *,
    emit: Callable[..., None],
    build_agent: Callable[..., ReActAgent],
    tool_fns: List[object] | None,
    tool_dispatch: Dict[str, object] | None,
    prompts: Mapping[str, Any],
    model_cfg: Mapping[str, Any],
    feature_flags: Mapping[str, Any],
    story_cfg: Mapping[str, Any],
    characters: Mapping[str, Any],
    world: Any,
) -> None:
    """Run the NPC talk demo (sequential group chat, no GM/adjudication)."""

    story_positions: Dict[str, Tuple[int, int]] = {}

    def _ingest_positions(raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        for actor_name, pos in raw.items():
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                try:
                    story_positions[str(actor_name)] = (int(pos[0]), int(pos[1]))
                except Exception:
                    continue

    if isinstance(story_cfg, dict):
        _ingest_positions(story_cfg.get("initial_positions") or {})
        _ingest_positions(story_cfg.get("positions") or {})
        initial_section = story_cfg.get("initial")
        if isinstance(initial_section, dict):
            _ingest_positions(initial_section.get("positions") or {})

    def _apply_story_position(name: str) -> None:
        pos = story_positions.get(str(name))
        if not pos:
            return
        try:
            world.set_position(name, pos[0], pos[1])
        except Exception:
            pass

    doctor_persona = prompts.get("player_persona") or _DEFAULT_DOCTOR_PERSONA
    npc_prompt_tpl = _join_lines(prompts.get("npc_prompt_template"))
    name_map = prompts.get("name_map") or {}

    # Build actors from configs or fallback
    char_cfg = dict(characters or {})
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
    # Participants resolution per request: derive purely from story positions that were ingested
    # into `story_positions` (supports top-level initial_positions/positions 或 initial.positions)。
    # If none present, fallback to default pair (Amiya, Doctor).
    allowed_names: List[str] = list(story_positions.keys())
    allowed_names_str = ", ".join(allowed_names) if allowed_names else ""

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

    # Tool list must be provided by caller (main). Keep empty default.
    tool_list = list(tool_fns) if tool_fns is not None else []

    if allowed_names:
        for name in allowed_names:
            entry = (char_cfg.get(name) or {}) if isinstance(char_cfg, dict) else {}
            # Stat block
            dnd = entry.get("dnd") or {}
            try:
                if dnd:
                    world.set_dnd_character(
                        name=name,
                        level=int(dnd.get("level", 1)),
                        ac=int(dnd.get("ac", 10)),
                        abilities=dnd.get("abilities") or {},
                        max_hp=int(dnd.get("max_hp", 8)),
                        proficient_skills=dnd.get("proficient_skills") or [],
                        proficient_saves=dnd.get("proficient_saves") or [],
                        move_speed=int(dnd.get("move_speed", 6)),
                    )
                else:
                    # Ensure the character exists even without dnd config
                    world.set_dnd_character(
                        name=name,
                        level=1,
                        ac=10,
                        abilities={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
                        max_hp=10,
                        proficient_skills=[],
                        proficient_saves=[],
                        move_speed=6,
                    )
            except Exception:
                pass
            _apply_story_position(name)
            persona = entry.get("persona") or (doctor_persona if name == "Doctor" else "一个简短的人设描述")
            appearance = entry.get("appearance")
            quotes = entry.get("quotes")
            agent = build_agent(
                name,
                persona,
                model_cfg,
                prompt_template=npc_prompt_tpl,
                allowed_names=allowed_names_str,
                appearance=appearance,
                quotes=quotes,
                relation_brief=_relation_brief(name),
                tools=tool_list,
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
                    world.set_dnd_character(
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
            _apply_story_position(name)
    # No fallback to default protagonists; if story provides no positions, run without participants.

    for nm in story_positions:
        try:
            if nm in (world.runtime().get("positions") or {}):
                continue
        except Exception:
            pass
        _apply_story_position(nm)

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
                    world.set_relation(str(src), str(dst), score, reason="配置设定")
                except Exception:
                    pass

    # Ensure Doctor exists only if he is a participant
    try:
        world_chars = world.runtime().get("characters") or {}
    except Exception:
        world_chars = {}
    if "Doctor" in allowed_names and "Doctor" not in world_chars:
        world.set_dnd_character(
            name="Doctor",
            level=1,
            ac=14,
            abilities={"STR": 12, "DEX": 16, "CON": 14, "INT": 10, "WIS": 14, "CHA": 10},
            max_hp=12,
            proficient_skills=["athletics", "insight", "medicine"],
            proficient_saves=["STR", "DEX"],
            move_speed=6,
        )

    # Scene setup sourced from story config if possible
    scene_cfg = story_cfg.get("scene") if isinstance(story_cfg, dict) else {}
    scene_name = None
    scene_objectives: List[str] = []
    if isinstance(scene_cfg, dict):
        name_candidate = scene_cfg.get("name")
        if isinstance(name_candidate, str) and name_candidate.strip():
            scene_name = name_candidate.strip()
        objs = scene_cfg.get("objectives")
        if isinstance(objs, list):
            for obj in objs:
                if isinstance(obj, str) and obj.strip():
                    scene_objectives.append(obj.strip())
    if not scene_name:
        scene_name = "旧城区·北侧仓棚"
    if not scene_objectives:
        scene_objectives = ["冲突终结"]
    world.set_scene(scene_name, scene_objectives)

    current_round = 0

    def _emit(
        event_type: str,
        *,
        actor: Optional[str] = None,
        phase: Optional[str] = None,
        turn: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = dict(data or {})
        emit(event_type=event_type, actor=actor, phase=phase, turn=turn if turn is not None else (current_round or None), data=payload)

    async def _bcast(hub: MsgHub, msg: Msg, *, phase: Optional[str] = None):
        await hub.broadcast(msg)
        text = _safe_text(msg)
        _emit(
            "narrative",
            actor=msg.name,
            phase=phase,
            data={"text": text, "role": getattr(msg, "role", None)},
        )

    TOOL_CALL_PATTERN = re.compile(r"CALL_TOOL\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<body>.*)\)")

    # Centralised tool dispatch mapping (must be injected by caller)
    TOOL_DISPATCH = dict(tool_dispatch or {})
    allowed_set = {str(n) for n in (allowed_names or [])}

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
                    "error",
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
            # Enforce that tool params refer to known participants only
            def _bad_actor(val: object) -> str | None:
                if not allowed_set:
                    return None
                if isinstance(val, str) and val not in allowed_set:
                    return val
                return None
            name_keys = {
                "perform_attack": ["attacker", "defender"],
                "auto_engage": ["attacker", "defender"],
                "perform_skill_check": ["name"],
                "advance_position": ["name"],
                "adjust_relation": ["a", "b"],
                "transfer_item": ["target"],
            }.get(tool_name, [])
            invalid = None
            for k in name_keys:
                invalid = _bad_actor(params.get(k))
                if invalid:
                    break
            if invalid:
                _emit(
                    "error",
                    actor=origin.name,
                    phase=phase,
                    data={
                        "message": f"无效角色名：{invalid}",
                        "tool": tool_name,
                        "params": params,
                        "allowed": sorted(allowed_set),
                        "error_type": "invalid_actor",
                    },
                )
                await _bcast(hub, Msg("Host", f"无效角色名：{invalid}。合法参与者：{', '.join(sorted(allowed_set))}", "assistant"), phase=phase)
                continue
            _emit(
                "tool_call",
                actor=origin.name,
                phase=phase,
                data={"tool": tool_name, "params": params},
            )
            try:
                resp = func(**params)
            except TypeError as exc:
                _emit(
                    "error",
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
                    "error",
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
                "tool_result",
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

    # Human-readable header for participants and starting positions
    _start_pos_lines = []
    try:
        for nm in allowed_names:
            pos = story_positions.get(nm)
            if pos:
                _start_pos_lines.append(f"{nm}({pos[0]}, {pos[1]})")
    except Exception:
        _start_pos_lines = []
    _participants_header = (
        "参与者：" + (", ".join(allowed_names) if allowed_names else "(无)") +
        (" | 初始坐标：" + "; ".join(_start_pos_lines) if _start_pos_lines else "")
    )

    async with MsgHub(
        participants=list(participants_order),
        announcement=Msg(
            "Host",
            "旧城区·北侧仓棚。铁梁回声震耳，每名战斗者都盯紧了自己的对手——退路已绝，只能分出胜负！\n" + _participants_header,
            "assistant",
        ),
    ) as hub:
        await sequential_pipeline(npcs_list)
        _emit("state_update", phase="initial", data={"state": world.snapshot()})
        round_idx = 1
        try:
            _cfg_val = int(feature_flags.get("max_rounds")) if isinstance(feature_flags, dict) else None
            max_rounds = _cfg_val if (_cfg_val is not None and _cfg_val > 0) else None
        except Exception:
            max_rounds = None

        def _objectives_resolved() -> bool:
            snap = world.snapshot()
            objs = list(snap.get("objectives") or [])
            if not objs:
                return False
            status = snap.get("objective_status") or {}
            for nm in objs:
                st = str(status.get(str(nm), "pending"))
                if st not in {"done", "blocked"}:
                    return False
            return True

        end_reason: Optional[str] = None
        # Default to original semantics: no hostiles -> end
        require_hostiles = bool(feature_flags.get("require_hostiles", True))

        def _is_alive(nm: str) -> bool:
            try:
                chars = world.snapshot().get("characters", {}) or {}
                st = chars.get(str(nm), {})
                return int(st.get("hp", 1)) > 0
            except Exception:
                return True

        def _living_field_names() -> List[str]:
            # Prefer participants; else those with positions; else all characters
            base: List[str]
            if allowed_names:
                base = list(allowed_names)
            else:
                snap = world.snapshot()
                base = list((snap.get("positions") or {}).keys()) or list((snap.get("characters") or {}).keys())
            return [n for n in base if _is_alive(n)]

        def _hostiles_present(threshold: int = -10) -> bool:
            names = _living_field_names()
            if len(names) <= 1:
                return False
            snap_rel = (world.snapshot().get("relations") or {})
            for i, a in enumerate(names):
                for b in names[i+1:]:
                    try:
                        sc_ab = int(snap_rel.get(f"{str(a)}->{str(b)}", 0))
                    except Exception:
                        sc_ab = 0
                    try:
                        sc_ba = int(snap_rel.get(f"{str(b)}->{str(a)}", 0))
                    except Exception:
                        sc_ba = 0
                    if sc_ab <= threshold or sc_ba <= threshold:
                        return True
            return False
        while True:
            try:
                rt = world.runtime()
                hdr_round_val = int(rt.get("round") or round_idx)
                hdr_round = hdr_round_val if bool(rt.get("in_combat")) else round_idx
            except Exception:
                hdr_round = round_idx
            current_round = hdr_round
            await _bcast(
                hub,
                Msg("Host", f"第{hdr_round}回合：小队行动", "assistant"),
                phase="round-start",
            )
            try:
                turn = world.get_turn()
                meta = turn.metadata or {}
                rnd = int(meta.get("round") or round_idx)
                if bool((world.runtime().get("in_combat"))):
                    current_round = rnd
            except Exception:
                pass

            try:
                rt = world.runtime()
                positions = rt.get("positions", {})
                in_combat = bool(rt.get("in_combat"))
                r_avail = rt.get("turn_state", {})
                _emit(
                    "state_update",
                    phase="turn-state",
                    data={
                        "positions": {k: list(v) for k, v in positions.items()},
                        "in_combat": in_combat,
                        "reaction_available": r_avail,
                    },
                )
            except Exception as exc:
                _emit(
                    "error",
                    phase="turn-state",
                    data={
                        "message": f"获取回合信息失败: {exc}",
                        "error_type": "turn_snapshot",
                    },
                )

            snapshot = world.snapshot()
            _emit("state_update", phase="world", turn=current_round, data={"state": snapshot})
            await _bcast(
                hub,
                Msg("Host", _world_summary_text(snapshot), "assistant"),
                phase="world-summary",
            )

            # If无敌对，则退出战斗模式但不强制结束整体流程（除非显式要求）
            if not _hostiles_present():
                try:
                    if bool(world.runtime().get("in_combat")):
                        world.end_combat()
                except Exception:
                    pass
                if require_hostiles:
                    end_reason = "场上已无敌对存活单位"
                    break

            combat_cleared = False
            for agent in npcs_list:
                name = getattr(agent, 'name', '')
                # Skip turn if the character is down (hp <= 0)
                try:
                    sheet = (world.snapshot().get("characters") or {}).get(name, {})
                    if int(sheet.get('hp', 1)) <= 0:
                        _emit(
                            "turn_start",
                            actor=name,
                            turn=current_round,
                            phase="actor-turn",
                            data={"round": current_round, "skipped": True, "reason": "down"},
                        )
                        _emit(
                            "turn_end",
                            actor=name,
                            turn=current_round,
                            phase="actor-turn",
                            data={"round": current_round, "skipped": True},
                        )
                        continue
                except Exception:
                    pass

                try:
                    reset = world.reset_actor_turn(name)
                except Exception:
                    reset = None
                try:
                    st_meta = (reset.metadata or {}).get('state') if reset else None
                except Exception:
                    st_meta = None
                _emit(
                    "turn_start",
                    actor=name,
                    turn=current_round,
                    phase="actor-turn",
                    data={
                        "round": current_round,
                        "state": st_meta,
                    },
                )

                out = await agent(None)
                await _bcast(
                    hub,
                    out,
                    phase=f"npc:{name or agent.__class__.__name__}",
                )
                await _handle_tool_calls(out, hub)
                # After each action, if无敌对则退出战斗但继续对话流程
                if not _hostiles_present():
                    try:
                        if bool(world.runtime().get("in_combat")):
                            world.end_combat()
                    except Exception:
                        pass
                    if require_hostiles:
                        end_reason = "场上已无敌对存活单位"
                        combat_cleared = True
                        break
                _emit(
                    "turn_end",
                    actor=name,
                    turn=current_round,
                    phase="actor-turn",
                    data={"round": current_round},
                )

            _emit("turn_end", phase="round", turn=current_round, data={"round": current_round})
            if combat_cleared:
                break
            round_idx += 1

            if _objectives_resolved():
                end_reason = "所有目标均已解决"
                break
            if max_rounds is not None and round_idx > max_rounds:
                end_reason = f"已达到最大回合 {max_rounds}"
                break

        final_snapshot = world.snapshot()
        _emit("state_update", phase="final", data={"state": final_snapshot})
        await _bcast(
            hub,
            Msg("Host", f"自动演算结束。{('(' + end_reason + ')') if end_reason else ''}", "assistant"),
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
