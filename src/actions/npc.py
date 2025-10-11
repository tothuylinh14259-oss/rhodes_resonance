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
      - attack_roll_dnd(...)
      - skill_check_dnd(...)
      - move_towards(...)
      - set_relation(...)
      - grant_item(...)
    """

    def perform_attack(
        attacker,
        defender,
        ability: str = "STR",
        proficient: bool = False,
        target_ac: Optional[int] = None,
        damage_expr: str = "1d4+STR",
        advantage: str = "none",
        auto_move: bool = False,
    ):
        resp = world.attack_roll_dnd(
            attacker=attacker,
            defender=defender,
            ability=ability,
            proficient=proficient,
            target_ac=target_ac,
            damage_expr=damage_expr,
            advantage=advantage,
            auto_move=auto_move,
        )
        meta = resp.metadata or {}
        hit = meta.get("hit")
        dmg = meta.get("damage_total")
        hp_before = meta.get("hp_before")
        hp_after = meta.get("hp_after")
        _log_action(
            f"attack {attacker} -> {defender} | hit={hit} dmg={dmg} hp:{hp_before}->{hp_after} "
            f"reach_ok={meta.get('reach_ok')} auto_move={auto_move}"
        )
        return resp

    def auto_engage(
        attacker,
        defender,
        ability: str = "STR",
        proficient: bool = False,
        target_ac: Optional[int] = None,
        damage_expr: str = "1d4+STR",
        advantage: str = "none",
    ):
        return perform_attack(
            attacker=attacker,
            defender=defender,
            ability=ability,
            proficient=proficient,
            target_ac=target_ac,
            damage_expr=damage_expr,
            advantage=advantage,
            auto_move=True,
        )

    def perform_skill_check(name, skill, dc, advantage: str = "none"):
        resp = world.skill_check_dnd(name=name, skill=skill, dc=dc, advantage=advantage)
        meta = resp.metadata or {}
        success = meta.get("success")
        total = meta.get("total")
        roll = meta.get("roll")
        _log_action(
            f"skill_check {name} skill={skill} dc={dc} -> success={success} total={total} roll={roll}"
        )
        return resp

    def advance_position(name, target, steps):
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
        _log_action(
            f"move {name} -> {tgt} steps={steps} moved={meta.get('moved')} remaining={meta.get('remaining')}"
        )
        return resp

    def adjust_relation(a, b, value, reason: str = ""):
        # Set relation to an absolute target value instead of applying a delta
        resp = world.set_relation(a, b, int(value), reason or "")
        meta = resp.metadata or {}
        _log_action(
            f"relation {a}->{b} set={value} score={meta.get('score')} reason={reason or '无'}"
        )
        return resp

    def transfer_item(target, item, n: int = 1):
        resp = world.grant_item(target=target, item=item, n=int(n))
        meta = resp.metadata or {}
        _log_action(
            f"transfer item={item} -> {target} qty={n} total={meta.get('count')}"
        )
        return resp

    tool_list: List[object] = [
        perform_attack,
        auto_engage,
        advance_position,
        adjust_relation,
        transfer_item,
    ]
    tool_dispatch: Dict[str, object] = {
        "perform_attack": perform_attack,
        "advance_position": advance_position,
        "adjust_relation": adjust_relation,
        "transfer_item": transfer_item,
        "auto_engage": auto_engage,
    }

    return tool_list, tool_dispatch


__all__ = ["make_npc_actions"]
