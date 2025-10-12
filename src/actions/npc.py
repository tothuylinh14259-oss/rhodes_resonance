from __future__ import annotations

"""
Domain-level action wrappers via dependency injection.

This module exposes a factory `make_npc_actions(world=...)` which returns
- a tool list for agent registration, and
- a tool dispatch dict for central orchestration.

No import-time dependency on other components (e.g. world).
"""

import logging
from typing import Optional, Any, Tuple, List, Dict


_ACTION_LOGGER = logging.getLogger("npc_talk_demo")


def _log_action(msg: str) -> None:
    try:
        if not msg:
            return
        _ACTION_LOGGER.info(f"[ACTION] {msg}")
    except Exception:
        pass


def make_npc_actions(*, world: Any) -> Tuple[List[object], Dict[str, object]]:
    """Create action tools bound to a provided world API (duck-typed).

    The `world` object is expected to provide functions:
      - attack_with_weapon(...)
      - skill_check_dnd(...)
      - move_towards(...)
      - set_relation(...)
      - grant_item(...)
      - set_guard(...)
      - clear_guard(...)
    """

    def perform_attack(
        attacker,
        defender,
        weapon: str,
        reason: str = "",
    ):
        # New path: weapon-driven attack; reach/ability/damage are sourced from weapon defs.
        resp = world.attack_with_weapon(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
        )
        meta = resp.metadata or {}
        hit = meta.get("hit")
        dmg = meta.get("damage_total")
        hp_before = meta.get("hp_before")
        hp_after = meta.get("hp_after")
        # Append human-visible reason and store for auditing
        reason_text = (str(reason).strip() or "未提供")
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
        except Exception:
            pass
        try:
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"attack {attacker} -> {defender} using {meta.get('weapon_id')} | hit={hit} dmg={dmg} hp:{hp_before}->{hp_after} "
            f"reach_ok={meta.get('reach_ok')} reason={reason_text}"
        )
        return resp

    # auto_engage removed: attacks no longer auto-move; call advance_position() explicitly before perform_attack().


    def perform_skill_check(name, skill, dc, advantage: str = "none", reason: str = ""):
        resp = world.skill_check_dnd(name=name, skill=skill, dc=dc, advantage=advantage)
        meta = resp.metadata or {}
        success = meta.get("success")
        total = meta.get("total")
        roll = meta.get("roll")
        # Append reason for traceability
        reason_text = (str(reason).strip() or "未提供")
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"skill_check {name} skill={skill} dc={dc} -> success={success} total={total} roll={roll} reason={reason_text}"
        )
        return resp

    def advance_position(name, target, steps, reason: str = ""):
        if isinstance(target, dict):
            tx = target.get("x", 0)
            ty = target.get("y", 0)
            tgt = (int(tx), int(ty))
        elif isinstance(target, (list, tuple)) and len(target) >= 2:
            tgt = (int(target[0]), int(target[1]))
        else:
            tgt = (0, 0)
        resp = world.move_towards(name=name, target=tgt, steps=int(steps))
        meta = resp.metadata or {}
        reason_text = (str(reason).strip() or "未提供")
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"move {name} -> {tgt} steps={steps} moved={meta.get('moved')} remaining={meta.get('remaining')} reason={reason_text}"
        )
        return resp

    def adjust_relation(a, b, value, reason: str = ""):
        # Set relation to an absolute target value instead of applying a delta
        resp = world.set_relation(a, b, int(value), reason or "")
        meta = resp.metadata or {}
        # world.set_relation 已在 content 中包含“理由：...”，此处仅补元数据字段以统一
        try:
            meta["call_reason"] = (str(reason).strip() or "未提供")
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"relation {a}->{b} set={value} score={meta.get('score')} reason={reason or '无'}"
        )
        return resp

    def transfer_item(target, item, n: int = 1, reason: str = ""):
        resp = world.grant_item(target=target, item=item, n=int(n))
        meta = resp.metadata or {}
        reason_text = (str(reason).strip() or "未提供")
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"transfer item={item} -> {target} qty={n} total={meta.get('count')} reason={reason_text}"
        )
        return resp

    def set_protection(guardian: str, protectee: str, reason: str = ""):
        resp = world.set_guard(guardian, protectee)
        meta = resp.metadata or {}
        reason_text = (str(reason).strip() or "未提供")
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(f"protect {guardian} -> {protectee} reason={reason_text}")
        return resp

    def clear_protection(guardian: str = "", protectee: str = "", reason: str = ""):
        g = guardian if guardian else None
        p = protectee if protectee else None
        resp = world.clear_guard(g, p)
        meta = resp.metadata or {}
        reason_text = (str(reason).strip() or "未提供")
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(f"clear_protect guardian={g} protectee={p} reason={reason_text}")
        return resp

    tool_list: List[object] = [
        perform_attack,
        advance_position,
        adjust_relation,
        transfer_item,
        set_protection,
        clear_protection,
    ]
    tool_dispatch: Dict[str, object] = {
        "perform_attack": perform_attack,
        "advance_position": advance_position,
        "adjust_relation": adjust_relation,
        "transfer_item": transfer_item,
        "set_protection": set_protection,
        "clear_protection": clear_protection,
    }

    return tool_list, tool_dispatch


__all__ = ["make_npc_actions"]
