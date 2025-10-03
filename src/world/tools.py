# Minimal world state and tools for the demo; designed to be pure and easy to test.
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Tuple, Any, List, Optional, Set
import random
from agentscope.tool import ToolResponse
from agentscope.message import TextBlock


def _rel_key(a: str, b: str) -> Tuple[str, str]:
    return tuple(sorted([a, b]))  # undirected relation


@dataclass
class World:
    time_min: int = 8 * 60  # 08:00 in minutes
    weather: str = "sunny"
    relations: Dict[Tuple[str, str], int] = field(default_factory=dict)
    inventory: Dict[str, Dict[str, int]] = field(default_factory=dict)
    characters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    location: str = "罗德岛·会议室"
    objectives: List[str] = field(default_factory=list)
    objective_status: Dict[str, str] = field(default_factory=dict)
    objective_notes: Dict[str, str] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    tension: int = 1  # 0-5
    marks: List[str] = field(default_factory=list)
    # --- Combat (D&D-like, 6s rounds) ---
    in_combat: bool = False
    round: int = 1
    turn_idx: int = 0
    initiative_order: List[str] = field(default_factory=list)
    initiative_scores: Dict[str, int] = field(default_factory=dict)
    # per-turn tokens/state for each name
    turn_state: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # default walking speeds (ft). If empty, 30ft is assumed.
    speeds: Dict[str, int] = field(default_factory=dict)
    # range bands between pairs (engaged/near/far/long)
    range_bands: Dict[Tuple[str, str], str] = field(default_factory=dict)
    # simple cover levels per character
    cover: Dict[str, str] = field(default_factory=dict)
    # conditions per character (hidden/prone/grappled/restrained/readying/...)
    conditions: Dict[str, Set[str]] = field(default_factory=dict)
    # lightweight triggers queue (ready/opportunity_attack, etc.)
    triggers: List[Dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "time_min": self.time_min,
            "weather": self.weather,
            "relations": {f"{a}&{b}": v for (a, b), v in self.relations.items()},
            "inventory": self.inventory,
            "characters": self.characters,
            "location": self.location,
            "objectives": list(self.objectives),
            "objective_status": dict(self.objective_status),
            "objective_notes": dict(self.objective_notes),
            "tension": int(self.tension),
            "marks": list(self.marks),
            "combat": {
                "in_combat": bool(self.in_combat),
                "round": int(self.round),
                "turn_idx": int(self.turn_idx),
                "initiative": list(self.initiative_order),
                "initiative_scores": dict(self.initiative_scores),
                "turn_state": {k: dict(v) for k, v in self.turn_state.items()},
                "range_bands": {f"{a}&{b}": v for (a, b), v in self.range_bands.items()},
            },
        }


WORLD = World()

# --- tools ---

def advance_time(mins: int):
    """Advance in-game time by a number of minutes.

    Args:
        mins: Minutes to advance (positive integer).

    Returns:
        dict: { ok: bool, time_min: int }
    """
    WORLD.time_min += int(mins)
    res = {"ok": True, "time_min": WORLD.time_min}
    blocks = [TextBlock(type="text", text=f"时间推进 {int(mins)} 分钟，当前时间(分钟)={WORLD.time_min}")]
    # Auto process events due
    try:
        ev = process_events()
        if ev and ev.content:
            blocks.extend(ev.content)
    except Exception:
        pass
    return ToolResponse(content=blocks, metadata=res)


def change_relation(a: str, b: str, delta: int, reason: str = ""):
    """Adjust relation score between two characters.

    Args:
        a: Character A name.
        b: Character B name.
        delta: Relation change (can be negative).
        reason: Optional description for auditing.

    Returns:
        dict: { ok: bool, pair: [str,str], score: int, reason: str }
    """
    k = _rel_key(a, b)
    WORLD.relations[k] = WORLD.relations.get(k, 0) + int(delta)
    res = {"ok": True, "pair": list(k), "score": WORLD.relations[k], "reason": reason}
    return ToolResponse(
        content=[TextBlock(type="text", text=f"关系调整 {k[0]}↔{k[1]}：{int(delta)}，当前分数={WORLD.relations[k]}。原因：{reason}")],
        metadata=res,
    )


def grant_item(target: str, item: str, n: int = 1):
    """Give items to a target's inventory.

    Args:
        target: Target name (NPC/player).
        item: Item id or name.
        n: Quantity to add (default 1).

    Returns:
        dict: { ok: bool, target: str, item: str, count: int }
    """
    bag = WORLD.inventory.setdefault(target, {})
    bag[item] = bag.get(item, 0) + int(n)
    res = {"ok": True, "target": target, "item": item, "count": bag[item]}
    return ToolResponse(
        content=[TextBlock(type="text", text=f"给予 {target} 物品 {item} x{int(n)}，现有数量={bag[item]}")],
        metadata=res,
    )


def describe_world(detail: bool = False):
    """Return a human-readable summary of the world state for agents.

    Args:
        detail: If True, include more verbose lines. (Metadata always contains the raw snapshot.)

    Returns:
        ToolResponse with a text summary and metadata as the raw snapshot dict.
    """
    snap = WORLD.snapshot()
    # Format time
    t = int(snap.get("time_min", 0))
    hh, mm = t // 60, t % 60
    time_str = f"{hh:02d}:{mm:02d}"
    weather = snap.get("weather", "unknown")
    # Relations
    rels = snap.get("relations", {}) or {}
    try:
        rel_lines = [f"{k}:{v}" for k, v in rels.items()]
    except Exception:
        rel_lines = []
    # Inventory
    inv = snap.get("inventory", {}) or {}
    inv_lines = []
    try:
        for who, bag in inv.items():
            if not bag:
                continue
            inv_lines.append(
                f"{who}[" + ", ".join(f"{it}:{cnt}" for it, cnt in bag.items()) + "]"
            )
    except Exception:
        pass

    # Scene & objectives
    loc = snap.get("location", "未知")
    objs = snap.get("objectives", []) or []
    stmap = snap.get("objective_status", {}) or {}
    def _fmt_obj(o):
        name = o if isinstance(o, str) else str(o)
        st = stmap.get(name)
        return f"{name}({st})" if st else str(name)
    obj_line = "; ".join(_fmt_obj(o) for o in objs) if objs else "(无)"

    # Characters summary
    chars = snap.get("characters", {}) or {}
    char_lines: List[str] = []
    try:
        for nm, st in chars.items():
            hp = st.get("hp")
            max_hp = st.get("max_hp")
            if hp is not None and max_hp is not None:
                char_lines.append(f"{nm}(HP {hp}/{max_hp})")
    except Exception:
        pass

    # Combat line (if any)
    combat = snap.get("combat") or {}
    combat_line = None
    try:
        if combat.get("in_combat") and combat.get("initiative"):
            order = combat.get("initiative") or []
            try:
                cur = order[int(combat.get("turn_idx") or 0)] if order else None
            except Exception:
                cur = None
            combat_line = f"战斗：R{int(combat.get('round') or 1)} 当前 {cur if cur else '(未定)'}；先攻顺序=" + ", ".join(order)
    except Exception:
        combat_line = None

    lines = [
        f"地点：{loc}",
        f"目标：{obj_line}",
        f"时间：{time_str}",
        f"天气：{weather}",
        ("关系：" + "; ".join(rel_lines)) if rel_lines else "关系：无变动",
        ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
        ("角色：" + "; ".join(char_lines)) if char_lines else "角色：未登记",
    ]
    if combat_line:
        lines.insert(1, combat_line)
    if detail:
        lines.append("(详情见元数据)")

    text = "\n".join(lines)
    return ToolResponse(content=[TextBlock(type="text", text=text)], metadata=snap)


def set_scene(location: str, objectives: Optional[List[str]] = None, append: bool = False):
    """Set the current scene location and optionally objectives list.

    Args:
        location: 新地点描述
        objectives: 目标列表；append=True 时为追加，否则替换
        append: 是否在现有目标后追加
    """
    WORLD.location = str(location)
    if objectives is not None:
        items = list(objectives)
        if append:
            WORLD.objectives.extend(items)
        else:
            WORLD.objectives = items
        for o in items:
            WORLD.objective_status[str(o)] = WORLD.objective_status.get(str(o), "pending")
    text = f"设定场景：{WORLD.location}；目标：{'; '.join(WORLD.objectives) if WORLD.objectives else '(无)'}"
    return ToolResponse(content=[TextBlock(type="text", text=text)], metadata=WORLD.snapshot())


def add_objective(obj: str):
    """Append a single objective into the world's objectives list."""
    name = str(obj)
    WORLD.objectives.append(name)
    WORLD.objective_status[name] = WORLD.objective_status.get(name, "pending")
    text = f"新增目标：{name}"
    return ToolResponse(content=[TextBlock(type="text", text=text)], metadata={"objectives": list(WORLD.objectives), "status": dict(WORLD.objective_status)})


# ---- D&D combat (initiative/turn economy) ----
def _dex_mod_of(name: str) -> int:
    st = WORLD.characters.get(name, {})
    try:
        dex = int(st.get("abilities", {}).get("DEX", 10))
    except Exception:
        dex = 10
    return _mod(dex)


def set_speed(name: str, feet: int = 30):
    WORLD.speeds[str(name)] = int(feet)
    return ToolResponse(content=[TextBlock(type="text", text=f"速度设定：{name} {int(feet)}ft")], metadata={"name": name, "speed": int(feet)})


def roll_initiative(participants: Optional[List[str]] = None):
    names = list(participants or list(WORLD.characters.keys()))
    import random as _rand
    scores: Dict[str, int] = {}
    for nm in names:
        # d20 + DEX
        sc = _rand.randint(1, 20) + _dex_mod_of(nm)
        scores[nm] = sc
    # sort desc by score; tiebreaker by DEX then name
    ordered = sorted(names, key=lambda n: (scores.get(n, 0), _dex_mod_of(n), str(n)), reverse=True)
    WORLD.initiative_scores = scores
    WORLD.initiative_order = ordered
    WORLD.round = 1
    WORLD.turn_idx = 0
    WORLD.in_combat = True
    # reset tokens for first actor
    _reset_turn_tokens_for(_current_actor_name())
    txt = "先攻：" + ", ".join(f"{n}({scores[n]})" for n in ordered)
    return ToolResponse(content=[TextBlock(type="text", text=txt)], metadata={"initiative": ordered, "scores": scores})


def start_combat(participants: Optional[List[str]] = None):
    res = roll_initiative(participants)
    return ToolResponse(content=[TextBlock(type="text", text="进入战斗模式")] + (res.content or []), metadata=res.metadata)


def end_combat():
    WORLD.in_combat = False
    WORLD.initiative_order.clear()
    WORLD.initiative_scores.clear()
    WORLD.turn_state.clear()
    WORLD.range_bands.clear()
    WORLD.cover.clear()
    WORLD.conditions.clear()
    WORLD.triggers.clear()
    return ToolResponse(content=[TextBlock(type="text", text="战斗结束")], metadata={"in_combat": False})


def _current_actor_name() -> Optional[str]:
    try:
        if not WORLD.in_combat:
            return None
        order = WORLD.initiative_order
        if not order:
            return None
        idx = int(WORLD.turn_idx)
        if idx < 0 or idx >= len(order):
            return None
        return order[idx]
    except Exception:
        return None


def _reset_turn_tokens_for(name: Optional[str]):
    if not name:
        return
    spd = int(WORLD.speeds.get(name, 30))
    WORLD.turn_state[name] = {
        "action_used": False,
        "bonus_used": False,
        "reaction_available": True,
        "move_left": spd,
        "disengage": False,
        "dodge": False,
        "help_target": None,
        "ready": None,  # {trigger: str, action: dict}
    }
    # clear short-lived conditions at the start of the actor's turn
    try:
        clear_condition(name, "dodge")
    except Exception:
        pass


def next_turn():
    if not WORLD.in_combat or not WORLD.initiative_order:
        return ToolResponse(content=[TextBlock(type="text", text="未处于战斗中")], metadata={"in_combat": False})
    WORLD.turn_idx += 1
    if WORLD.turn_idx >= len(WORLD.initiative_order):
        WORLD.turn_idx = 0
        WORLD.round += 1
    _reset_turn_tokens_for(_current_actor_name())
    cur = _current_actor_name() or "(未定)"
    return ToolResponse(content=[TextBlock(type="text", text=f"回合推进：R{WORLD.round} 轮到 {cur}")], metadata={"round": WORLD.round, "actor": cur})


def get_turn() -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=f"当前：R{WORLD.round} idx={WORLD.turn_idx} actor={_current_actor_name() or '(未定)'}")], metadata={
        "round": WORLD.round,
        "turn_idx": WORLD.turn_idx,
        "actor": _current_actor_name(),
        "order": list(WORLD.initiative_order),
        "state": dict(WORLD.turn_state.get(_current_actor_name() or "", {})),
    })


def use_action(name: str, kind: str = "action") -> ToolResponse:
    nm = str(name)
    st = WORLD.turn_state.setdefault(nm, {})
    if kind == "action":
        if st.get("action_used"):
            return ToolResponse(content=[TextBlock(type="text", text=f"[已用] {nm} 本回合动作已用完")], metadata={"ok": False})
        st["action_used"] = True
        return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 使用 动作")], metadata={"ok": True})
    if kind == "bonus":
        if st.get("bonus_used"):
            return ToolResponse(content=[TextBlock(type="text", text=f"[已用] {nm} 本回合附赠动作已用完")], metadata={"ok": False})
        st["bonus_used"] = True
        return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 使用 附赠动作")], metadata={"ok": True})
    if kind == "reaction":
        if not st.get("reaction_available", True):
            return ToolResponse(content=[TextBlock(type="text", text=f"[已用] {nm} 本轮反应不可用")], metadata={"ok": False})
        st["reaction_available"] = False
        return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 使用 反应")], metadata={"ok": True})
    return ToolResponse(content=[TextBlock(type="text", text=f"未知动作类型 {kind}")], metadata={"ok": False})


def consume_movement(name: str, feet: int) -> ToolResponse:
    nm = str(name)
    st = WORLD.turn_state.setdefault(nm, {})
    left = int(st.get("move_left", WORLD.speeds.get(nm, 30)))
    feet = int(feet)
    if feet <= 0:
        return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 不移动")], metadata={"ok": True, "left": left})
    if feet > left:
        st["move_left"] = 0
        return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 试图移动 {feet}ft，但仅剩 {left}ft；按 {left}ft 计算")], metadata={"ok": False, "left": 0})
    st["move_left"] = left - feet
    return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 移动 {feet}ft（剩余 {st['move_left']}ft）")], metadata={"ok": True, "left": st["move_left"]})


# ---- Range bands & cover/conditions ----
BANDS = ["engaged", "near", "far", "long"]


def set_range_band(a: str, b: str, band: str):
    band = str(band)
    if band not in BANDS:
        return ToolResponse(content=[TextBlock(type="text", text=f"未知距离带 {band}")], metadata={"ok": False})
    k = _rel_key(a, b)
    WORLD.range_bands[k] = band
    return ToolResponse(content=[TextBlock(type="text", text=f"距离：{k[0]}↔{k[1]} = {band}")], metadata={"ok": True, "pair": list(k), "band": band})


def get_range_band(a: str, b: str) -> str:
    return WORLD.range_bands.get(_rel_key(a, b), "near")


def _band_steps(fr: str, to: str) -> int:
    try:
        i1 = BANDS.index(fr); i2 = BANDS.index(to)
        return abs(i1 - i2)
    except Exception:
        return 0


def _band_cost(fr: str, to: str) -> int:
    # engaged<->near:5; near<->far:30; far<->long:60; accumulate steps
    if fr == to:
        return 0
    idx1 = BANDS.index(fr); idx2 = BANDS.index(to)
    lo, hi = sorted([idx1, idx2])
    cost = 0
    for i in range(lo, hi):
        a = BANDS[i]; b = BANDS[i + 1]
        if (a, b) == ("engaged", "near"):
            cost += 5
        elif (a, b) == ("near", "far"):
            cost += 30
        else:
            cost += 60
    return cost


def move_to_band(actor: str, target: str, band: str):
    cur = get_range_band(actor, target)
    need = _band_cost(cur, band)
    was_engaged = cur == "engaged" and band != "engaged"
    res = consume_movement(actor, need)
    ok = bool((res.metadata or {}).get("ok", True))
    if ok:
        set_range_band(actor, target, band)
        # leaving engagement may provoke OA (queued here; KP消费)
        if was_engaged and not (WORLD.turn_state.get(actor, {}).get("disengage")):
            queue_trigger("opportunity_attack", {"attacker": target, "provoker": actor})
    return ToolResponse(content=(res.content or []), metadata={"ok": ok, "cost": need, "from": cur, "to": band})


def set_cover(name: str, level: str):
    level = str(level)
    if level not in ("none", "half", "three_quarters", "total"):
        return ToolResponse(content=[TextBlock(type="text", text=f"未知掩体等级 {level}")], metadata={"ok": False})
    WORLD.cover[str(name)] = level
    return ToolResponse(content=[TextBlock(type="text", text=f"掩体：{name} -> {level}")], metadata={"ok": True, "name": name, "cover": level})


def get_cover(name: str) -> str:
    return WORLD.cover.get(str(name), "none")


def apply_condition(name: str, cond: str):
    s = WORLD.conditions.setdefault(str(name), set())
    s.add(str(cond))
    return ToolResponse(content=[TextBlock(type="text", text=f"状态：{name} +{cond}")], metadata={"ok": True, "name": name, "cond": cond})


def clear_condition(name: str, cond: str):
    s = WORLD.conditions.setdefault(str(name), set())
    if cond in s:
        s.remove(cond)
    return ToolResponse(content=[TextBlock(type="text", text=f"状态：{name} -{cond}")], metadata={"ok": True, "name": name, "cond": cond})


def has_condition(name: str, cond: str) -> bool:
    return str(cond) in WORLD.conditions.get(str(name), set())


def queue_trigger(kind: str, payload: Optional[Dict[str, Any]] = None):
    WORLD.triggers.append({"kind": str(kind), "payload": dict(payload or {})})
    return ToolResponse(content=[TextBlock(type="text", text=f"触发：{kind}")], metadata={"queued": len(WORLD.triggers)})


def pop_triggers() -> List[Dict[str, Any]]:
    out = list(WORLD.triggers)
    WORLD.triggers.clear()
    return out


def get_ac(name: str) -> int:
    try:
        return int(WORLD.characters.get(str(name), {}).get("ac", 10))
    except Exception:
        return 10


def cover_bonus(name: str) -> Tuple[int, bool]:
    """Return (ac_bonus, total_cover_blocked)."""
    c = get_cover(name)
    if c == "half":
        return 2, False
    if c == "three_quarters":
        return 5, False
    if c == "total":
        return 0, True
    return 0, False


def advantage_for_attack(attacker: str, defender: str) -> str:
    """Compute net advantage from simple conditions.
    +1: attacker hidden; +1: defender prone; -1: defender dodge.
    Return 'advantage' | 'disadvantage' | 'none'.
    """
    score = 0
    if has_condition(attacker, "hidden"):
        score += 1
    if has_condition(defender, "prone"):
        score += 1
    if has_condition(defender, "dodge"):
        score -= 1
    if score > 0:
        return "advantage"
    if score < 0:
        return "disadvantage"
    return "none"


# ---- Standard actions (thin wrappers) ----
def act_dash(name: str):
    nm = str(name)
    use_action(nm, "action")
    st = WORLD.turn_state.setdefault(nm, {})
    spd = int(WORLD.speeds.get(nm, 30))
    st["move_left"] = int(st.get("move_left", spd)) + spd
    return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 冲刺（移动力+{spd}ft）")], metadata={"ok": True, "move_left": st["move_left"]})


def act_disengage(name: str):
    nm = str(name)
    use_action(nm, "action")
    st = WORLD.turn_state.setdefault(nm, {})
    st["disengage"] = True
    return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 脱离接触（本回合移动不引发借机攻击）")], metadata={"ok": True})


def act_dodge(name: str):
    nm = str(name)
    use_action(nm, "action")
    st = WORLD.turn_state.setdefault(nm, {})
    st["dodge"] = True
    return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 闪避架势（直到下回合开始，被攻击者判定处于不利）")], metadata={"ok": True})


def act_help(name: str, target: str):
    nm = str(name)
    use_action(nm, "action")
    st = WORLD.turn_state.setdefault(nm, {})
    st["help_target"] = str(target)
    return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 协助 {target}（其下一次检定或攻击获得优势）")], metadata={"ok": True, "target": target})


def act_hide(name: str, dc: int = 13):
    # Perform stealth check; on success, grant hidden
    nm = str(name)
    res = skill_check_dnd(nm, "stealth", int(dc))
    success = bool((res.metadata or {}).get("success"))
    out = list(res.content or [])
    if success:
        tr = apply_condition(nm, "hidden")
        out.extend(tr.content or [])
    return ToolResponse(content=out, metadata={"ok": success})


def act_search(name: str, skill: str = "perception", dc: int = 13):
    return skill_check_dnd(str(name), str(skill), int(dc))


def contest(a: str, a_skill: str, b: str, b_skill: str) -> ToolResponse:
    # Simple opposed check: d20 + (ability+prof)
    import random as _rand
    def _mod_skill(nm: str, sk: str) -> int:
        st = WORLD.characters.get(nm, {})
        ab_name = SKILL_TO_ABILITY.get(sk.lower())
        ab = int(st.get("abilities", {}).get(ab_name or "STR", 10))
        mod = _mod(ab)
        prof = int(st.get("prof", 2)) if (sk.lower() in (st.get("proficient_skills") or [])) else 0
        return mod + prof
    a_base = _mod_skill(a, a_skill)
    b_base = _mod_skill(b, b_skill)
    a_roll = _rand.randint(1,20) + a_base
    b_roll = _rand.randint(1,20) + b_base
    text = f"对抗：{a} {a_skill}={a_roll} vs {b} {b_skill}={b_roll} -> {'{a}胜' if a_roll>=b_roll else '{b}胜'}"
    return ToolResponse(content=[TextBlock(type="text", text=text)], metadata={"a_total": a_roll, "b_total": b_roll, "a_base": a_base, "b_base": b_base, "winner": a if a_roll>=b_roll else b})


def act_grapple(attacker: str, defender: str) -> ToolResponse:
    res = contest(attacker, "athletics", defender, "athletics")
    winner = res.metadata.get("winner") if res.metadata else None
    out = list(res.content or [])
    if winner == attacker:
        tr = apply_condition(defender, "grappled")
        out.extend(tr.content or [])
    return ToolResponse(content=out, metadata={"ok": winner == attacker})


def act_shove(attacker: str, defender: str, mode: str = "prone") -> ToolResponse:
    res = contest(attacker, "athletics", defender, "acrobatics")
    winner = res.metadata.get("winner") if res.metadata else None
    out = list(res.content or [])
    if winner == attacker:
        if mode == "prone":
            tr = apply_condition(defender, "prone")
            out.extend(tr.content or [])
        else:
            # push: move defender one band away if known vs attacker
            cur = get_range_band(attacker, defender)
            try:
                idx = BANDS.index(cur)
                target_band = BANDS[min(idx+1, len(BANDS)-1)]
            except Exception:
                target_band = "near"
            mv = move_to_band(defender, attacker, target_band)
            out.extend(mv.content or [])
    return ToolResponse(content=out, metadata={"ok": winner == attacker})


def act_ready(name: str, trigger: str, reaction_action: Dict[str, Any]) -> ToolResponse:
    nm = str(name)
    use_action(nm, "action")
    st = WORLD.turn_state.setdefault(nm, {})
    st["ready"] = {"trigger": str(trigger or ""), "action": dict(reaction_action or {})}
    return ToolResponse(content=[TextBlock(type="text", text=f"{nm} 预备：{trigger}")], metadata={"ok": True})


# ---- Character/stat tools ----
def set_character(name: str, hp: int, max_hp: int):
    """Create/update a character with hp and max_hp."""
    WORLD.characters[name] = {"hp": int(hp), "max_hp": int(max_hp)}
    return ToolResponse(
        content=[TextBlock(type="text", text=f"设定角色 {name}：HP {int(hp)}/{int(max_hp)}")],
        metadata={"name": name, "hp": int(hp), "max_hp": int(max_hp)},
    )


def get_character(name: str):
    st = WORLD.characters.get(name, {})
    if not st:
        return ToolResponse(content=[TextBlock(type="text", text=f"未找到角色 {name}")], metadata={"found": False})
    hp = st.get("hp"); max_hp = st.get("max_hp")
    return ToolResponse(
        content=[TextBlock(type="text", text=f"{name}: HP {hp}/{max_hp}")],
        metadata={"found": True, **st},
    )


def damage(name: str, amount: int):
    amt = max(0, int(amount))
    st = WORLD.characters.setdefault(name, {"hp": 0, "max_hp": 0})
    st["hp"] = max(0, int(st.get("hp", 0)) - amt)
    dead = st["hp"] <= 0
    return ToolResponse(
        content=[TextBlock(type="text", text=f"{name} 受到 {amt} 伤害，HP {st['hp']}/{st.get('max_hp', st['hp'])}{'（倒地）' if dead else ''}")],
        metadata={"name": name, "hp": st["hp"], "max_hp": st.get("max_hp"), "dead": dead},
    )


def heal(name: str, amount: int):
    amt = max(0, int(amount))
    st = WORLD.characters.setdefault(name, {"hp": 0, "max_hp": 0})
    max_hp = int(st.get("max_hp", 0))
    st["hp"] = min(max_hp if max_hp > 0 else st.get("hp", 0), int(st.get("hp", 0)) + amt)
    return ToolResponse(
        content=[TextBlock(type="text", text=f"{name} 恢复 {amt} 点生命，HP {st['hp']}/{st.get('max_hp', st['hp'])}")],
        metadata={"name": name, "hp": st["hp"], "max_hp": st.get("max_hp")},
    )


# ---- Dice tools ----
def roll_dice(expr: str = "1d20"):
    """Roll dice expression like '1d20+3', '2d6+1', 'd20'."""
    expr = expr.lower().replace(" ", "")
    total = 0
    parts: List[str] = []
    i = 0
    sign = 1
    # Simple parser supporting NdM, +/-, and constants
    token = ""
    tokens: List[str] = []
    for ch in expr:
        if ch in "+-":
            if token:
                tokens.append(token)
                token = ""
            tokens.append(ch)
        else:
            token += ch
    if token:
        tokens.append(token)
    # Evaluate tokens
    sign = 1
    breakdown: List[str] = []
    for tk in tokens:
        if tk == "+":
            sign = 1
            continue
        if tk == "-":
            sign = -1
            continue
        if "d" in tk:
            n_str, _, m_str = tk.partition("d")
            n = int(n_str) if n_str else 1
            m = int(m_str) if m_str else 20
            rolls = [random.randint(1, m) for _ in range(max(1, n))]
            subtotal = sum(rolls) * sign
            total += subtotal
            breakdown.append(f"{sign:+d}{n}d{m}({','.join(map(str, rolls))})")
        else:
            val = sign * int(tk)
            total += val
            breakdown.append(f"{val:+d}")
    text = f"掷骰 {expr} = {total} [{' '.join(breakdown)}]"
    return ToolResponse(
        content=[TextBlock(type="text", text=text)],
        metadata={"expr": expr, "total": total, "breakdown": breakdown},
    )


def skill_check(target: int, modifier: int = 0, advantage: str = "none"):
    """Perform a d20 skill check.

    Args:
        target: DC/目标值（越高越难）
        modifier: 调整值，如敏捷/感知等加成
        advantage: 'none'|'advantage'|'disadvantage'
    """
    def d20():
        return random.randint(1, 20)
    r1 = d20(); r2 = d20()
    if advantage == "advantage":
        roll = max(r1, r2)
        note = f"优势({r1},{r2}->取{roll})"
    elif advantage == "disadvantage":
        roll = min(r1, r2)
        note = f"劣势({r1},{r2}->取{roll})"
    else:
        roll = r1
        note = f"单掷({roll})"
    total = roll + int(modifier)
    success = total >= int(target)
    text = f"检定 d20+{int(modifier)}={total} vs DC {int(target)} -> {'成功' if success else '失败'}（{note}）"
    return ToolResponse(
        content=[TextBlock(type="text", text=text)],
        metadata={"roll": roll, "modifier": int(modifier), "total": total, "target": int(target), "success": success, "note": note},
    )


def resolve_melee_attack(attacker: str, defender: str, atk_mod: int = 0, dc: int = 12, dmg_expr: str = "1d4", advantage: str = "none"):
    """Resolve a simple melee attack: d20+atk_mod vs DC, on success apply damage.

    Args:
        attacker: 攻击发起者名字
        defender: 防御者名字
        atk_mod: 攻击加值（如力量/技巧）
        dc: 防御难度（DC）
        dmg_expr: 伤害骰表达式（如 1d4, 1d6+1）
        advantage: 'none'|'advantage'|'disadvantage'
    """
    # Attack roll
    atk_res = skill_check(target=int(dc), modifier=int(atk_mod), advantage=advantage)
    success = bool(atk_res.metadata.get("success")) if atk_res.metadata else False
    parts: list[TextBlock] = []
    # Describe the attack roll
    parts.append(TextBlock(type="text", text=f"攻击检定：{attacker} d20+{int(atk_mod)} vs DC {int(dc)} -> {'成功' if success else '失败'}"))
    if success:
        # Damage roll (reuse roll_dice)
        dmg_res = roll_dice(dmg_expr)
        total = int(dmg_res.metadata.get("total", 0)) if dmg_res.metadata else 0
        # Apply damage
        dmg_apply = damage(defender, total)
        # Aggregate logs
        parts.append(TextBlock(type="text", text=f"伤害掷骰 {dmg_expr} -> {total}"))
        # Append the damage text line
        if dmg_apply.content:
            for blk in dmg_apply.content:
                if blk.get("type") == "text":
                    parts.append(blk)
    out_meta = {
        "attacker": attacker,
        "defender": defender,
        "attack": atk_res.metadata,
    }
    return ToolResponse(content=parts, metadata=out_meta)


# ================= D&D-like stat block support =================
ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
SKILL_TO_ABILITY = {
    "acrobatics": "DEX",
    "animal handling": "WIS",
    "arcana": "INT",
    "athletics": "STR",
    "deception": "CHA",
    "history": "INT",
    "insight": "WIS",
    "intimidation": "CHA",
    "investigation": "INT",
    "medicine": "WIS",
    "nature": "INT",
    "perception": "WIS",
    "performance": "CHA",
    "persuasion": "CHA",
    "religion": "INT",
    "sleight of hand": "DEX",
    "stealth": "DEX",
    "survival": "WIS",
}


def _mod(score: int) -> int:
    return (int(score) - 10) // 2


def set_dnd_character(
    name: str,
    level: int,
    ac: int,
    abilities: Dict[str, int],
    max_hp: int,
    proficient_skills: Optional[List[str]] = None,
    proficient_saves: Optional[List[str]] = None,
) -> ToolResponse:
    """Create/update a D&D-style character sheet (simplified).

    abilities: dict with STR/DEX/CON/INT/WIS/CHA as keys.
    """
    sheet = WORLD.characters.setdefault(name, {})
    sheet.update({
        "level": int(level),
        "ac": int(ac),
        "abilities": {k.upper(): int(v) for k, v in abilities.items()},
        "hp": int(max_hp),
        "max_hp": int(max_hp),
        "prof": 2 + max(0, int(level) - 1) // 4,  # L1-4:+2,5-8:+3,...
        "proficient_skills": [s.lower() for s in (proficient_skills or [])],
        "proficient_saves": [s.upper() for s in (proficient_saves or [])],
    })
    # Keep legacy keys for compatibility
    WORLD.characters[name]["hp"] = sheet["hp"]
    WORLD.characters[name]["max_hp"] = sheet["max_hp"]
    return ToolResponse(
        content=[TextBlock(type="text", text=f"设定 {name}（Lv{sheet['level']} AC {sheet['ac']} HP {sheet['hp']}/{sheet['max_hp']}）")],
        metadata={"name": name, **sheet},
    )


def get_stat_block(name: str) -> ToolResponse:
    st = WORLD.characters.get(name, {})
    if not st:
        return ToolResponse(content=[TextBlock(type="text", text=f"未找到 {name}")], metadata={"found": False})
    ab = st.get("abilities", {})
    ab_line = ", ".join(
        f"{k} {v} ({_signed(_mod(v))})" for k, v in ab.items() if k in ABILITIES
    )
    txt = (
        f"{name} Lv{st.get('level',1)} AC {st.get('ac','?')} "
        f"HP {st.get('hp','?')}/{st.get('max_hp','?')}\n"
        f"属性：{ab_line}\n"
        f"熟练：+{st.get('prof',2)}"
    )
    return ToolResponse(content=[TextBlock(type="text", text=txt)], metadata=st)


def skill_check_dnd(name: str, skill: str, dc: int, advantage: str = "none") -> ToolResponse:
    st = WORLD.characters.get(name, {})
    ab_name = SKILL_TO_ABILITY.get(skill.lower())
    if not ab_name:
        return ToolResponse(content=[TextBlock(type="text", text=f"未知技能 {skill}")], metadata={"success": False})
    ab = int(st.get("abilities", {}).get(ab_name, 10))
    mod = _mod(ab)
    prof = int(st.get("prof", 2)) if skill.lower() in (st.get("proficient_skills") or []) else 0
    base = mod + prof
    base_note = f"{ab_name}修正{_signed(mod)}{' 熟练+%d'%prof if prof else ''}"
    roll_res = skill_check(target=int(dc), modifier=base, advantage=advantage)
    # rewrite first line to include actor name
    out = []
    if roll_res.content:
        for i, blk in enumerate(roll_res.content):
            if i == 0 and blk.get("type") == "text":
                out.append(TextBlock(type="text", text=f"{name} 技能检定（{skill}）：{blk.get('text')}（{base_note}）"))
            else:
                out.append(blk)
    return ToolResponse(content=out, metadata={"actor": name, "skill": skill, **(roll_res.metadata or {})})


def saving_throw_dnd(name: str, ability: str, dc: int, advantage: str = "none") -> ToolResponse:
    st = WORLD.characters.get(name, {})
    ab_name = ability.upper()
    ab = int(st.get("abilities", {}).get(ab_name, 10))
    mod = _mod(ab)
    prof = int(st.get("prof", 2)) if ab_name in (st.get("proficient_saves") or []) else 0
    base = mod + prof
    base_note = f"{ab_name}修正{_signed(mod)}{' 熟练+%d'%prof if prof else ''}"
    roll_res = skill_check(target=int(dc), modifier=base, advantage=advantage)
    out = []
    if roll_res.content:
        for i, blk in enumerate(roll_res.content):
            if i == 0 and blk.get("type") == "text":
                out.append(TextBlock(type="text", text=f"{name} 豁免检定（{ab_name}）：{blk.get('text')}（{base_note}）"))
            else:
                out.append(blk)
    return ToolResponse(content=out, metadata={"actor": name, "save": ab_name, **(roll_res.metadata or {})})


def attack_roll_dnd(
    attacker: str,
    defender: str,
    ability: str = "STR",
    proficient: bool = False,
    target_ac: Optional[int] = None,
    damage_expr: str = "1d4+STR",
    advantage: str = "none",
) -> ToolResponse:
    """D&D-like attack roll: d20 + ability mod (+prof) vs AC, on hit apply damage.
    damage_expr 支持 +STR/+DEX/+CON 等修正占位符。
    """
    atk = WORLD.characters.get(attacker, {})
    dfd = WORLD.characters.get(defender, {})
    ac = int(target_ac if target_ac is not None else dfd.get("ac", 10))
    mod = _mod(int(atk.get("abilities", {}).get(ability.upper(), 10)))
    prof = int(atk.get("prof", 2)) if proficient else 0
    base = mod + prof
    # Attack roll
    atk_res = skill_check(target=int(ac), modifier=base, advantage=advantage)
    success = bool(atk_res.metadata.get("success")) if atk_res.metadata else False
    parts: List[TextBlock] = []
    parts.append(TextBlock(type="text", text=f"攻击：{attacker} d20{_signed(base)} vs AC {ac} -> {'命中' if success else '未中'}"))
    hp_before = int(WORLD.characters.get(defender, {}).get("hp", dfd.get("hp", 0)))
    dmg_total = 0
    if success:
        # Replace ability placeholders in damage expr
        dmg_expr2 = _replace_ability_tokens(damage_expr, mod)
        dmg_res = roll_dice(dmg_expr2)
        total = int(dmg_res.metadata.get("total", 0)) if dmg_res.metadata else 0
        dmg_total = total
        dmg_apply = damage(defender, total)
        parts.append(TextBlock(type="text", text=f"伤害：{dmg_expr2} -> {total}"))
        for blk in dmg_apply.content or []:
            if blk.get("type") == "text":
                parts.append(blk)
    hp_after = int(WORLD.characters.get(defender, {}).get("hp", dfd.get("hp", 0)))
    return ToolResponse(content=parts, metadata={
        "attacker": attacker,
        "defender": defender,
        "hit": success,
        "base": base,
        "damage_total": int(dmg_total),
        "hp_before": int(hp_before),
        "hp_after": int(hp_after),
    })


def _replace_ability_tokens(expr: str, ability_mod: int) -> str:
    # Very simple: replace any of +STR/+DEX/+CON... with the provided mod
    s = expr
    for ab in ABILITIES:
        token = ab
        if token in s:
            s = s.replace(token, str(ability_mod))
    return s


def _signed(x: int) -> str:
    return f"+{x}" if x >= 0 else str(x)


# ---- Objective status helpers ----
def complete_objective(name: str, note: str = ""):
    nm = str(name)
    if nm not in WORLD.objectives:
        WORLD.objectives.append(nm)
    WORLD.objective_status[nm] = "done"
    if note:
        WORLD.objective_notes[nm] = note
    return ToolResponse(content=[TextBlock(type="text", text=f"目标完成：{nm}")], metadata={"objectives": list(WORLD.objectives), "status": dict(WORLD.objective_status)})

def block_objective(name: str, reason: str = ""):
    nm = str(name)
    if nm not in WORLD.objectives:
        WORLD.objectives.append(nm)
    WORLD.objective_status[nm] = "blocked"
    if reason:
        WORLD.objective_notes[nm] = reason
    suffix = f"，原因：{reason}" if reason else ""
    return ToolResponse(content=[TextBlock(type="text", text=f"目标受阻：{nm}{suffix}")], metadata={"objectives": list(WORLD.objectives), "status": dict(WORLD.objective_status)})

# ---- Event clock ----
def schedule_event(name: str, at_min: int, note: str = "", effects: Optional[List[Dict[str, Any]]] = None):
    WORLD.events.append({"name": str(name), "at": int(at_min), "note": str(note), "effects": list(effects or [])})
    WORLD.events.sort(key=lambda x: x.get("at", 0))
    return ToolResponse(content=[TextBlock(type="text", text=f"计划事件：{name}@{int(at_min)}分钟")], metadata={"queued": len(WORLD.events)})

def process_events():
    outputs: List[TextBlock] = []
    due = [ev for ev in WORLD.events if int(ev.get("at", 0)) <= WORLD.time_min]
    WORLD.events = [ev for ev in WORLD.events if int(ev.get("at", 0)) > WORLD.time_min]
    for ev in due:
        name = ev.get("name", "(事件)")
        note = ev.get("note", "")
        outputs.append(TextBlock(type="text", text=f"[事件] {name}：{note}")) if note else outputs.append(TextBlock(type="text", text=f"[事件] {name}"))
        for eff in (ev.get("effects") or []):
            try:
                kind = eff.get("kind")
                if kind == "add_objective":
                    add_objective(str(eff.get("name")))
                elif kind == "complete_objective":
                    complete_objective(str(eff.get("name")))
                elif kind == "block_objective":
                    block_objective(str(eff.get("name")), str(eff.get("reason", "")))
                elif kind == "relation":
                    a, b, d = eff.get("a"), eff.get("b"), int(eff.get("delta", 0))
                    if a and b:
                        change_relation(str(a), str(b), d, reason=str(eff.get("reason", "")))
                elif kind == "grant":
                    grant_item(str(eff.get("target")), str(eff.get("item")), int(eff.get("n", 1)))
                elif kind == "damage":
                    damage(str(eff.get("target")), int(eff.get("amount", 0)))
                elif kind == "heal":
                    heal(str(eff.get("target")), int(eff.get("amount", 0)))
            except Exception:
                outputs.append(TextBlock(type="text", text=f"[事件执行失败] {eff}"))
    if outputs:
        return ToolResponse(content=outputs, metadata={"fired": len(due)})
    return ToolResponse(content=[], metadata={"fired": 0})

# ---- Atmosphere helpers ----
def adjust_tension(delta: int):
    WORLD.tension = max(0, min(5, int(WORLD.tension) + int(delta)))
    return ToolResponse(content=[TextBlock(type="text", text=f"(气氛){'升' if delta>0 else '降' if delta<0 else '稳'}至 {WORLD.tension}")], metadata={"tension": WORLD.tension})

def add_mark(text: str):
    s = str(text or "").strip()
    if s:
        WORLD.marks.append(s)
        if len(WORLD.marks) > 10:
            WORLD.marks = WORLD.marks[-10:]
    return ToolResponse(content=[TextBlock(type="text", text=f"(环境刻痕)+{s}")], metadata={"marks": list(WORLD.marks)})
