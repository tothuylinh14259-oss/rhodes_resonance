#!/usr/bin/env python3
from __future__ import annotations

"""
Central Orchestrator (main layer)

职责：
- 加载配置、创建日志上下文；
- 构造 world 端口、actions 工具、agents 工厂；
- 通过依赖注入调用 run_demo（已内联自原 runtime.engine）。
"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Callable, Mapping
from pathlib import Path
from agentscope.agent import AgentBase, ReActAgent  # type: ignore
from agentscope.message import Msg  # type: ignore
from agentscope.pipeline import MsgHub, sequential_pipeline  # type: ignore
from dataclasses import asdict, is_dataclass

from actions.npc import make_npc_actions
import world.tools as world_impl
from eventlog import create_logging_context, Event, EventType
from settings.loader import (
    project_root,
    load_prompts,
    load_model_config,
    load_story_config,
    load_characters,
    load_weapons,
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
    set_dnd_character_from_config = staticmethod(world_impl.set_dnd_character_from_config)
    set_weapon_defs = staticmethod(world_impl.set_weapon_defs)
    attack_with_weapon = staticmethod(world_impl.attack_with_weapon)
    # participants and character meta helpers
    set_participants = staticmethod(world_impl.set_participants)
    set_character_meta = staticmethod(world_impl.set_character_meta)

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
            "participants": list(getattr(W, "participants", []) or []),
        }

def _join_lines(tpl):
    if isinstance(tpl, list):
        try:
            return "\n".join(str(x) for x in tpl)
        except Exception:
            return "\n".join(tpl)
    return tpl


# reach/attack range normalization moved to world.set_dnd_character_from_config


async def run_demo(
    *,
    emit: Callable[..., None],
    build_agent: Callable[..., ReActAgent],
    tool_fns: List[object] | None,
    tool_dispatch: Dict[str, object] | None,
    prompts: Mapping[str, Any],
    model_cfg: Mapping[str, Any],
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

    npc_prompt_tpl = _join_lines(prompts.get("npc_prompt_template"))


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
    # If none present, run without participants (no implicit fallback to any default pair).
    allowed_names: List[str] = list(story_positions.keys())
    # Persist participants to world so all downstream consumers read from world only
    try:
        world.set_participants(allowed_names)
    except Exception:
        pass
    try:
        allowed_names_world: List[str] = list(world.snapshot().get("participants") or [])
    except Exception:
        allowed_names_world = list(allowed_names)
    allowed_names_str = ", ".join(allowed_names_world) if allowed_names_world else ""

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
        """Build relation brief from world state, not raw config."""
        try:
            rel_map = dict(world.snapshot().get("relations") or {})
        except Exception:
            rel_map = {}
        if not rel_map:
            return ""
        me = str(name)
        entries: List[str] = []
        for key, raw in rel_map.items():
            try:
                a, b = key.split("->", 1)
            except Exception:
                continue
            if a != me or b == me:
                continue
            try:
                score = int(raw)
            except Exception:
                continue
            label = _relation_category(score)
            entries.append(f"{b}:{score:+d}（{label}）")
        return "；".join(entries)

    # Tool list must be provided by caller (main). Keep empty default.
    tool_list = list(tool_fns) if tool_fns is not None else []

    # Ensure character persona/appearance/quotes are stored in world for all actors
    try:
        for nm, entry in actor_entries.items():
            if not isinstance(entry, dict):
                continue
            try:
                world.set_character_meta(
                    nm,
                    persona=entry.get("persona"),
                    appearance=entry.get("appearance"),
                    quotes=entry.get("quotes"),
                )
            except Exception:
                pass
    except Exception:
        pass

    if allowed_names_world:
        for name in allowed_names_world:
            entry = (char_cfg.get(name) or {}) if isinstance(char_cfg, dict) else {}
            # Stat block
            dnd = entry.get("dnd") or {}
            try:
                if dnd:
                    # Use world normalizer for DnD config
                    world.set_dnd_character_from_config(name=name, dnd=dnd)
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
                        move_speed_steps=6,
                    )
            except Exception:
                pass
            _apply_story_position(name)
            # Load inventory (weapons as items) from character config
            try:
                inv = entry.get("inventory") or {}
                if isinstance(inv, dict):
                    for it, cnt in inv.items():
                        try:
                            world_impl.grant_item(target=name, item=str(it), n=int(cnt))
                        except Exception:
                            pass
            except Exception:
                pass
            # Build per-actor weapon brief for prompt
            def _weapon_brief_for(nm: str) -> str:
                try:
                    snap = world.snapshot()
                    wdefs = dict((snap.get("weapon_defs") or {}))
                    bag = dict((snap.get("inventory") or {}).get(str(nm), {}) or {})
                except Exception:
                    return "无"
                entries: List[str] = []
                for wid, count in bag.items():
                    if int(count) <= 0 or wid not in wdefs:
                        continue
                    wd = wdefs.get(wid) or {}
                    try:
                        rs = int(wd.get("reach_steps", 1))
                    except Exception:
                        rs = 1
                    dmg = wd.get("damage_expr", "1d4+STR")
                    entries.append(f"{wid}(触及 {rs}步, 伤害 {dmg})")
                return "；".join(entries) if entries else "无"
            # Read meta from world (single source of truth)
            try:
                sheet = (world.snapshot().get("characters") or {}).get(name, {}) or {}
            except Exception:
                sheet = {}
            persona = sheet.get("persona")
            if not isinstance(persona, str) or not persona.strip():
                raise ValueError(f"缺少角色人设(persona)：{name}")
            appearance = sheet.get("appearance")
            quotes = sheet.get("quotes")
            agent = build_agent(
                name,
                persona,
                model_cfg,
                prompt_template=npc_prompt_tpl,
                allowed_names=allowed_names_str,
                appearance=appearance,
                quotes=quotes,
                relation_brief=_relation_brief(name),
                weapon_brief=_weapon_brief_for(name),
                tools=tool_list,
            )
            npcs_list.append(agent)
            participants_order.append(agent)
        # preload non-participant actors (e.g., enemies) into world sheets
        for name, entry in actor_entries.items():
            if name in allowed_names_world:
                continue
            dnd = entry.get("dnd") or {}
            if dnd:
                try:
                    world.set_dnd_character_from_config(name=name, dnd=dnd)
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

    # Scene setup sourced from story config (time/weather/details from JSON; no hardcoded defaults)
    scene_cfg = story_cfg.get("scene") if isinstance(story_cfg, dict) else {}
    scene_name = None
    scene_objectives: List[str] = []
    scene_details: List[str] = []
    scene_weather: Optional[str] = None
    scene_time_min: Optional[int] = None
    if isinstance(scene_cfg, dict):
        name_candidate = scene_cfg.get("name")
        if isinstance(name_candidate, str) and name_candidate.strip():
            scene_name = name_candidate.strip()
        objs = scene_cfg.get("objectives")
        if isinstance(objs, list):
            for obj in objs:
                if isinstance(obj, str) and obj.strip():
                    scene_objectives.append(obj.strip())
        details_val = scene_cfg.get("details")
        if isinstance(details_val, str) and details_val.strip():
            scene_details = [details_val.strip()]
        elif isinstance(details_val, list):
            for d in details_val:
                if isinstance(d, str) and d.strip():
                    scene_details.append(d.strip())
        # Prefer HH:MM string if provided; fallback to time_min
        tstr = scene_cfg.get("time")
        if isinstance(tstr, str) and tstr:
            m = re.match(r"^(\d{1,2}):(\d{2})$", tstr.strip())
            if m:
                hh, mm = int(m.group(1)), int(m.group(2))
                if 0 <= hh < 24 and 0 <= mm < 60:
                    scene_time_min = hh * 60 + mm
        if scene_time_min is None:
            tm = scene_cfg.get("time_min", None)
            if isinstance(tm, (int, float)):
                try:
                    scene_time_min = int(tm)
                except Exception:
                    scene_time_min = None
        w = scene_cfg.get("weather")
        if isinstance(w, str) and w.strip():
            scene_weather = w.strip()
    # Apply story config if any; otherwise keep current world defaults
    if any([scene_name, scene_objectives, scene_details, scene_weather, scene_time_min is not None]):
        try:
            snap0 = world.snapshot()
            current_loc = str((snap0 or {}).get("location") or "")
        except Exception:
            current_loc = ""
        world.set_scene(
            scene_name or current_loc,
            scene_objectives or None,
            append=False,
            details=scene_details or None,
            time_min=scene_time_min,
            weather=scene_weather,
        )

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
        # record to in-memory chat log for recap (best-effort)
        try:
            CHAT_LOG.append({
                "actor": getattr(msg, "name", None),
                "role": getattr(msg, "role", None),
                "text": text,
                "turn": current_round,
                "phase": phase or "",
            })
        except Exception:
            pass

    # Accept both styles that agents may output:
    # 1) CALL_TOOL name({json})
    # 2) CALL_TOOL name\n{json}
    # Some models also append a suffix like ":3" after the tool name (e.g. for footnotes).
    # We therefore avoid a strict regex on parentheses and instead scan forward
    # for the next balanced JSON object after the tool name token.
    TOOL_CALL_PATTERN = re.compile(r"CALL_TOOL\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")

    # Centralised tool dispatch mapping (must be injected by caller)
    TOOL_DISPATCH = dict(tool_dispatch or {})
    allowed_set = {str(n) for n in (allowed_names_world or [])}
    # ---- In-memory mini logs for per-turn recap (broadcast to all participants) ----
    CHAT_LOG: List[Dict[str, Any]] = []     # {actor, role, text, turn, phase}
    ACTION_LOG: List[Dict[str, Any]] = []   # {actor, tool, type, text|params, meta, turn}
    LAST_SEEN: Dict[str, int] = {}          # per-actor chat index checkpoint
    # Recap settings: fixed defaults (feature flags removed)
    recap_enabled = True
    recap_msg_limit = 6
    recap_action_limit = 6

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

        def _extract_json_after(s: str, start_pos: int) -> Tuple[Optional[str], int]:
            """Return (json_text, end_index) for the first balanced {...} after start_pos.

            end_index points to the character index just after the closing brace,
            or start_pos if nothing could be parsed.
            """
            n = len(s)
            i = s.find("{", start_pos)
            if i == -1:
                return None, start_pos
            brace = 0
            in_str = False
            esc = False
            j = i
            while j < n:
                ch = s[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == '{':
                        brace += 1
                    elif ch == '}':
                        brace -= 1
                        if brace == 0:
                            return s[i : j + 1], j + 1
                j += 1
            return None, start_pos

        idx = 0
        while True:
            m = TOOL_CALL_PATTERN.search(text, idx)
            if not m:
                break
            name = m.group("name")
            # Skip any suffix like ":3" or whitespace/colon before the JSON
            scan_from = m.end()
            # Extract JSON object following the tool name (with or without parentheses)
            json_body, end_pos = _extract_json_after(text, scan_from)
            params: dict = {}
            if json_body:
                try:
                    params = json.loads(json_body)
                except Exception:
                    params = {}
                calls.append((name, params))
                idx = end_pos
            else:
                # No JSON body found; advance to avoid infinite loop
                idx = scan_from
        return calls

    def _strip_tool_calls_from_text(text: str) -> str:
        """Return `text` with all CALL_TOOL ... {json} segments removed.

        Compatible with both styles:
        - CALL_TOOL name({json})
        - CALL_TOOL name\n{json}
        Also tolerant to suffix like `:3` after tool name.
        """
        if not text:
            return text

        def _extract_json_after(s: str, start_pos: int) -> Tuple[Optional[str], int]:
            n = len(s)
            i = s.find("{", start_pos)
            if i == -1:
                return None, start_pos
            brace = 0
            in_str = False
            esc = False
            j = i
            while j < n:
                ch = s[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == '{':
                        brace += 1
                    elif ch == '}':
                        brace -= 1
                        if brace == 0:
                            return s[i : j + 1], j + 1
                j += 1
            return None, start_pos

        idx = 0
        out_parts: List[str] = []
        while True:
            m = TOOL_CALL_PATTERN.search(text, idx)
            if not m:
                out_parts.append(text[idx:])
                break
            # Keep text before the tool call
            out_parts.append(text[idx:m.start()])
            scan_from = m.end()
            # Remove the following JSON object if present
            json_body, end_pos = _extract_json_after(text, scan_from)
            if json_body:
                idx = end_pos
            else:
                idx = scan_from
        return "".join(out_parts)

    # --- Dev-only context snapshot: write a compact card per-actor to logs/<actor>_context_dev.log ---
    def _write_dev_context_card(name: str) -> None:
        """Append a human-friendly context card for `name` to its own log file.

        This does NOT broadcast to agents and does NOT affect memory.
        """
        try:
            # Build sections
            snap = world.snapshot()
            world_txt = _world_summary_text(snap)

            start = int(LAST_SEEN.get(name, 0))
            recent_msgs = [e for e in CHAT_LOG[start:] if e.get("actor") not in (None, "Host")]
            if recap_msg_limit > 0:
                recent_msgs = recent_msgs[-recap_msg_limit:]
            # Dev view no longer includes a separate Recent Actions block;
            # actions/results are already reflected in broadcast messages.

            rel_text = _relation_brief(name)

            lines: List[str] = []
            lines.append(f"=== Round {current_round} | Actor: {name} ===")
            if rel_text:
                lines.append(f"[Relation] {rel_text}")
            lines.append("[World]")
            lines.append(world_txt)
            if recent_msgs:
                lines.append("[Recent Messages]")
                for e in recent_msgs:
                    txt = str(e.get("text") or "").strip()
                    if len(txt) > 160:
                        txt = txt[:157] + "..."
                    lines.append(f"- {e.get('actor')}: {txt}")
            # (no Recent Actions section by design)

            # Write to logs/<actor>_context_dev.log
            logs_dir = project_root() / "logs"
            try:
                logs_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            # Use actor name directly (project uses ASCII names); fallback to safe filename
            safe = "".join(ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in str(name))
            path = logs_dir / f"{safe}_context_dev.log"
            with path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n\n")
        except Exception:
            # Dev utility is best-effort; never break the main loop
            pass

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
                ACTION_LOG.append({
                    "actor": origin.name,
                    "tool": tool_name,
                    "type": "call",
                    "params": dict(params or {}),
                    "turn": current_round,
                })
            except Exception:
                pass
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
            try:
                ACTION_LOG.append({
                    "actor": origin.name,
                    "tool": tool_name,
                    "type": "result",
                    "text": list(lines),
                    "meta": meta,
                    "turn": current_round,
                })
            except Exception:
                pass
            if not lines:
                continue
            tool_msg = Msg(
                name=f"{origin.name}[tool]",
                content="\n".join(line for line in lines if line),
                role="assistant",
            )
            await _bcast(hub, tool_msg, phase=phase)


    def _recap_for(name: str) -> Optional[Msg]:
        """Build a concise recap message for the upcoming actor, or None if empty/disabled.

        Recap includes up to N recent broadcasts (excluding Host-only boilerplate) and
        up to M recent tool results. The recap is broadcast to all participants.
        """
        if not recap_enabled:
            return None
        start = int(LAST_SEEN.get(name, 0))
        # Exclude pure Host messages to avoid duplicating world-summary headers
        recent_msgs = [e for e in CHAT_LOG[start:] if e.get("actor") not in (None, "Host")]
        if recap_msg_limit > 0:
            recent_msgs = recent_msgs[-recap_msg_limit:]
        # Drop the separate actions section from recap; messages already include tool results
        recent_actions = []
        if not recent_msgs:
            return None
        lines: List[str] = [f"系统回顾（供 {name} 决策）"]
        if recent_msgs:
            lines.append("最近播报：")
            for e in recent_msgs:
                txt = str(e.get("text") or "").strip()
                if len(txt) > 160:
                    txt = txt[:157] + "..."
                lines.append(f"- {e.get('actor')}: {txt}")
        # No separate actions block
        LAST_SEEN[name] = len(CHAT_LOG)
        return Msg("Host", "\\n".join(lines), "assistant")

    # Human-readable header for participants and starting positions
    _start_pos_lines = []
    try:
        parts = list(world.snapshot().get("participants") or [])
        pos_map = world.snapshot().get("positions") or {}
        for nm in parts:
            pos = pos_map.get(nm) or story_positions.get(nm)
            if pos:
                _start_pos_lines.append(f"{nm}({pos[0]}, {pos[1]})")
    except Exception:
        _start_pos_lines = []
    _participants_header = (
        "参与者：" + (", ".join(world.snapshot().get("participants") or []) if (world.snapshot().get("participants") or []) else "(无)") +
        (" | 初始坐标：" + "; ".join(_start_pos_lines) if _start_pos_lines else "")
    )

    # Opening text: read from configs, persist into world.scene_details (append) for single-source-of-truth
    opening_text: Optional[str] = None
    try:
        if isinstance(story_cfg, dict):
            sc = story_cfg.get("scene")
            if isinstance(sc, dict):
                txt = (sc.get("description") or sc.get("opening") or "")
                if isinstance(txt, str) and txt.strip():
                    opening_text = txt.strip()
    except Exception:
        opening_text = None
    default_opening = "旧城区·北侧仓棚。铁梁回声震耳，每名战斗者都盯紧了自己的对手——退路已绝，只能分出胜负！"
    opening_line = (opening_text or default_opening)
    # Append opening into world.scene_details if not already present
    try:
        snap0 = world.snapshot()
        current_loc = str((snap0 or {}).get("location") or "")
        details0 = list((snap0 or {}).get("scene_details") or [])
        if opening_line and opening_line not in details0:
            details_new = details0 + [opening_line]
            world.set_scene(current_loc, None, append=True, details=details_new)
    except Exception:
        pass
    announcement_text = opening_line + "\n" + _participants_header

    async with MsgHub(
        participants=list(participants_order),
        announcement=Msg(
            "Host",
            announcement_text,
            "assistant",
        ),
    ) as hub:
        await sequential_pipeline(npcs_list)
        _emit("state_update", phase="initial", data={"state": world.snapshot()})
        round_idx = 1
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
        # Default to original semantics: end when no hostiles (fixed behaviour)
        require_hostiles = True

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
            if allowed_names_world:
                base = list(allowed_names_world)
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

                # Inject a recap message for all participants before the actor decides
                try:
                    # Dev-only context card to per-actor log file
                    _write_dev_context_card(name)
                    recap_msg = _recap_for(name)
                    if recap_msg is not None:
                        await _bcast(hub, recap_msg, phase="context:recap")
                except Exception:
                    pass

                out = await agent(None)
                try:
                    raw_text = _safe_text(out)
                    cleaned = _strip_tool_calls_from_text(raw_text)
                    if cleaned and cleaned.strip():
                        msg_clean = Msg(getattr(out, "name", name), cleaned, getattr(out, "role", "assistant") or "assistant")
                        await _bcast(
                            hub,
                            msg_clean,
                            phase=f"npc:{name or agent.__class__.__name__}",
                        )
                except Exception:
                    # If anything goes wrong, fall back to broadcasting the original
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

    details = [d for d in (snap.get("scene_details") or []) if isinstance(d, str) and d.strip()]
    lines = [
        f"环境概要：地点 {location}；时间 {hh:02d}:{mm:02d}；天气 {weather}",
        ("目标：" + "; ".join((f"{str(o)}({obj_status.get(str(o))})" if obj_status.get(str(o)) else str(o)) for o in objectives)) if objectives else "目标：无",
        # 说明：避免使用“系统提示”措辞以免模型联想出系统旁白
        ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
        ("坐标：" + "; ".join(pos_lines)) if pos_lines else "坐标：未记录",
        ("角色：" + "; ".join(char_lines)) if char_lines else "角色：未登记",
    ]
    if details:
        # Insert details after the header line
        lines.insert(1, "环境细节：" + "；".join(details))
    return "\n".join(lines)



def main() -> None:
    print("============================================================")
    print("NPC Talk Demo (Orchestrator: main.py)")
    print("============================================================")

    # Load configs
    prompts = load_prompts()
    model_cfg_obj = load_model_config()
    story_cfg = load_story_config()
    characters = load_characters()
    weapons = load_weapons() or {}

    # Convert model config dataclass to mapping
    if is_dataclass(model_cfg_obj):
        model_cfg: Dict[str, Any] = asdict(model_cfg_obj)
    else:
        model_cfg = dict(getattr(model_cfg_obj, "__dict__", {}) or {})

    # Build logging context under project root
    root = project_root()
    log_ctx = create_logging_context(base_path=root)

    # Clean dev context logs at run start (mirror run_story/run_events overwrite)
    try:
        logs_dir = root / "logs"
        if logs_dir.exists():
            for _p in logs_dir.glob("*_context_dev.log"):
                try:
                    _p.unlink()  # remove; writer will recreate with append
                except Exception:
                    try:
                        _p.open("w", encoding="utf-8").close()  # fallback: truncate
                    except Exception:
                        pass
    except Exception:
        pass


    # Emit function adapter
    def emit(*, event_type: str, actor=None, phase=None, turn=None, data=None) -> None:
        ev = Event(event_type=EventType(event_type), actor=actor, phase=phase, turn=turn, data=dict(data or {}))
        log_ctx.bus.publish(ev)

    # Bind world and actions
    world = _WorldPort()
    # Load weapon table into world before tools are used
    try:
        world_impl.set_weapon_defs(weapons)
    except Exception:
        pass
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
