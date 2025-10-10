# Minimal world state and tools for the demo; designed to be pure and easy to test.
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Tuple, Any, List, Optional, Set, Union
import math
import random
try:
    from agentscope.tool import ToolResponse  # type: ignore
    from agentscope.message import TextBlock  # type: ignore
except Exception:  # Fallback minimal stubs when Agentscope is unavailable
    class ToolResponse:  # type: ignore
        def __init__(self, content=None, metadata=None):
            self.content = list(content or [])
            self.metadata = dict(metadata or {})

    class TextBlock(dict):  # type: ignore
        def __init__(self, type: str = "text", text: str = ""):
            super().__init__()
            self["type"] = type
            self["text"] = text


# --- Core grid configuration ---
# Distances use grid steps only (简称“步”).
DEFAULT_MOVE_SPEED_STEPS = 6  # standard humanoid walk in steps per turn
DEFAULT_REACH_STEPS = 1       # default melee reach in steps


def format_distance_steps(steps: int) -> str:
    """Format a grid distance for narration in steps, e.g., "6步"."""
    try:
        s = int(steps)
    except Exception:
        s = 0
    if s < 0:
        s = 0
    return f"{s}步"


def _default_move_steps() -> int:
    return int(DEFAULT_MOVE_SPEED_STEPS) if DEFAULT_MOVE_SPEED_STEPS > 0 else 1


def _pair_key(a: str, b: str) -> Tuple[str, str]:
    """Return a sorted key for undirected pair-based state."""
    return tuple(sorted([str(a), str(b)]))


def _rel_key(a: str, b: str) -> Tuple[str, str]:
    """Return a directed key representing a->b relation."""
    return str(a), str(b)


@dataclass
class World:
    time_min: int = 8 * 60  # 08:00 in minutes
    weather: str = "sunny"
    relations: Dict[Tuple[str, str], int] = field(default_factory=dict)
    inventory: Dict[str, Dict[str, int]] = field(default_factory=dict)
    characters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    positions: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    objective_positions: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    location: str = "罗德岛·会议室"
    objectives: List[str] = field(default_factory=list)
    objective_status: Dict[str, str] = field(default_factory=dict)
    objective_notes: Dict[str, str] = field(default_factory=dict)
    # Scene flavor/details lines to help agents ground their narration
    scene_details: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    tension: int = 1  # 0-5
    marks: List[str] = field(default_factory=list)
    # Compatibility: legacy field referenced by tests; remains a no-op container
    hidden_enemies: Dict[str, Any] = field(default_factory=dict)
    # --- Combat (D&D-like, 6s rounds) ---
    in_combat: bool = False
    round: int = 1
    turn_idx: int = 0
    initiative_order: List[str] = field(default_factory=list)
    initiative_scores: Dict[str, int] = field(default_factory=dict)
    # per-turn tokens/state for each name
    turn_state: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # default walking speeds stored as grid steps
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
            "relations": {f"{a}->{b}": v for (a, b), v in self.relations.items()},
            "inventory": self.inventory,
            "characters": self.characters,
            "positions": {k: list(v) for k, v in self.positions.items()},
            "objective_positions": {k: list(v) for k, v in self.objective_positions.items()},
            # removed hidden_enemies entirely per design (no implicit enemies)
            "location": self.location,
            "objectives": list(self.objectives),
            "scene_details": list(self.scene_details),
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
        content=[TextBlock(type="text", text=f"关系调整 {k[0]}->{k[1]}：{int(delta)}，当前分数={WORLD.relations[k]}。原因：{reason}")],
        metadata=res,
    )


def set_relation(a: str, b: str, value: int, reason: str = "初始化") -> ToolResponse:
    k = _rel_key(a, b)
    WORLD.relations[k] = int(value)
    res = {"ok": True, "pair": list(k), "score": WORLD.relations[k], "reason": reason}
    return ToolResponse(
        content=[TextBlock(type="text", text=f"关系设定 {k[0]}->{k[1]} = {WORLD.relations[k]}。原因：{reason}")],
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


def set_position(name: str, x: int, y: int) -> ToolResponse:
    """Set or update the grid position of an actor."""
    WORLD.positions[str(name)] = (int(x), int(y))
    refresh_range_bands_for(str(name))
    return ToolResponse(
        content=[TextBlock(type="text", text=f"设定 {name} 位置 -> ({int(x)}, {int(y)})")],
        metadata={"name": name, "position": [int(x), int(y)]},
    )


def get_position(name: str) -> ToolResponse:
    pos = WORLD.positions.get(str(name))
    if pos is None:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"未记录 {name} 的坐标")],
            metadata={"found": False},
        )
    return ToolResponse(
        content=[TextBlock(type="text", text=f"{name} 当前位置：({pos[0]}, {pos[1]})")],
        metadata={"found": True, "position": list(pos)},
    )


def set_objective_position(name: str, x: int, y: int) -> ToolResponse:
    WORLD.objective_positions[str(name)] = (int(x), int(y))
    return ToolResponse(
        content=[TextBlock(type="text", text=f"目标 {name} 坐标设为 ({int(x)}, {int(y)})")],
        metadata={"name": name, "position": [int(x), int(y)]},
    )


# Removed hidden-enemy utilities by request: use explicit participants/relations only.


def _grid_distance(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def get_distance_steps_between(name_a: str, name_b: str) -> Optional[int]:
    """Return grid steps between two actors; None if any position missing."""
    pa = WORLD.positions.get(str(name_a))
    pb = WORLD.positions.get(str(name_b))
    if pa is None or pb is None:
        return None
    return _grid_distance(pa, pb)


# Removed meter-based distance helper; use steps only (get_distance_steps_between).


def _band_for_steps(steps: int) -> str:
    if steps <= 1:
        return "engaged"
    if steps <= 6:
        return "near"
    if steps <= 12:
        return "far"
    return "long"


def refresh_range_bands_for(name: str) -> None:
    pos = WORLD.positions.get(str(name))
    if pos is None:
        return
    for other, o_pos in WORLD.positions.items():
        if other == str(name) or o_pos is None:
            continue
        band = _band_for_steps(_grid_distance(pos, o_pos))
        WORLD.range_bands[_pair_key(name, other)] = band


def get_move_speed_steps(name: str) -> int:
    sheet = WORLD.characters.get(name, {})
    try:
        val = sheet.get("move_speed_steps")
        if val is not None:
            return int(val)
    except Exception:
        pass
    return int(WORLD.speeds.get(name, _default_move_steps()))


def get_reach_steps(name: str) -> int:
    sheet = WORLD.characters.get(name, {})
    try:
        # Support both reach_* and attack_range_* as synonyms (steps only)
        val = sheet.get("reach_steps") or sheet.get("attack_range_steps") or sheet.get("reach") or sheet.get("attack_range")
        if val is not None:
            return max(1, int(val))
    except Exception:
        pass
    return max(1, int(DEFAULT_REACH_STEPS))


def move_towards(name: str, target: Tuple[int, int], steps: int) -> ToolResponse:
    """Move an actor toward target grid up to `steps` 4-way steps."""
    steps = max(0, int(steps))
    if steps == 0:
        pos = WORLD.positions.get(str(name)) or (0, 0)
        return ToolResponse(
            content=[TextBlock(type="text", text=f"{name} 保持在 ({pos[0]}, {pos[1]})，未移动。")],
            metadata={"moved": 0, "position": list(pos)},
        )
    current = WORLD.positions.get(str(name))
    if current is None:
        current = WORLD.positions[str(name)] = (0, 0)
    x, y = current
    tx, ty = int(target[0]), int(target[1])
    moved = 0
    while moved < steps and (x, y) != (tx, ty):
        if x != tx:
            x += 1 if tx > x else -1
        elif y != ty:
            y += 1 if ty > y else -1
        moved += 1
    WORLD.positions[str(name)] = (x, y)
    refresh_range_bands_for(str(name))
    remaining = _grid_distance((x, y), (tx, ty))
    reached = (x, y) == (tx, ty)
    text = (
        f"{name} 向 ({tx}, {ty}) 移动 {format_distance_steps(moved)}，现位于 ({x}, {y})。"
        + (" 已抵达目标。" if reached else f" 距目标还差 {format_distance_steps(remaining)}。")
    )
    return ToolResponse(
        content=[TextBlock(type="text", text=text)],
        metadata={
            "moved": moved,
            "reached": reached,
            "remaining": remaining,
            "position": [x, y],
            "moved_steps": moved,
            "remaining_steps": remaining,
        },
    )


def describe_world(detail: bool = False):
    """Return a human-readable summary of the world state for agents.

    Args:
        detail: If True, include more verbose lines. (Metadata always contains the raw snapshot.)

    Returns:
        ToolResponse with a text summary and metadata as the raw snapshot dict.
    """
    snap = WORLD.snapshot()
    view = dict(snap)
    view.pop("relations", None)
    # Format time (no longer shown in describe_world text, retained in metadata only)
    t = int(view.get("time_min", 0))
    hh, mm = t // 60, t % 60
    time_str = f"{hh:02d}:{mm:02d}"
    weather = view.get("weather", "unknown")
    details = [
        d for d in (view.get("scene_details") or []) if isinstance(d, str) and d.strip()
    ]
    # Inventory
    inv = view.get("inventory", {}) or {}
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
    loc = view.get("location", "未知")
    objs = view.get("objectives", []) or []
    stmap = view.get("objective_status", {}) or {}
    def _fmt_obj(o):
        name = o if isinstance(o, str) else str(o)
        st = stmap.get(name)
        return f"{name}({st})" if st else str(name)
    obj_line = "; ".join(_fmt_obj(o) for o in objs) if objs else "(无)"

    # Characters summary
    chars = view.get("characters", {}) or {}
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
    combat = view.get("combat") or {}
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

    # As per latest requirement, hide location/time/weather/environment details from describe_world output
    lines = [
        f"目标：{obj_line}",
        "关系：请回顾系统提示（按己方立场慎重行动）",
        ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
        ("角色：" + "; ".join(char_lines)) if char_lines else "角色：未登记",
    ]
    if combat_line:
        lines.insert(1, combat_line)
    if detail:
        lines.append("(详情见元数据)")

    text = "\n".join(lines)
    return ToolResponse(content=[TextBlock(type="text", text=text)], metadata=view)


def set_scene(
    location: str,
    objectives: Optional[List[str]] = None,
    append: bool = False,
    *,
    time_min: Optional[int] = None,
    time: Optional[str] = None,
    weather: Optional[str] = None,
    details: Optional[Union[str, List[str]]] = None,
):
    """Set the current scene and optionally update objectives/time/weather/details.

    Args:
        location: 新地点描述
        objectives: 目标列表；append=True 时为追加，否则替换
        append: 是否在现有目标后追加
        time_min: 以分钟表示的时间
        time: 字符串时间 "HH:MM"（若提供则优先生效）
        weather: 天气文本
        details: 细节文本或文本列表
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
    # Optional updates: weather
    if weather is not None:
        w = str(weather).strip()
        if w:
            WORLD.weather = w
    # Optional updates: time (prefer HH:MM string if provided)
    if isinstance(time, str) and time:
        s = time.strip()
        try:
            hh_str, mm_str = s.split(":")
            hh, mm = int(hh_str), int(mm_str)
            if 0 <= hh < 24 and 0 <= mm < 60:
                WORLD.time_min = hh * 60 + mm
        except Exception:
            pass
    elif time_min is not None:
        try:
            WORLD.time_min = max(0, int(time_min))
        except Exception:
            pass
    # Optional updates: scene details
    if details is not None:
        vals: List[str] = []
        if isinstance(details, str):
            s = details.strip()
            if s:
                vals = [s]
        elif isinstance(details, list):
            for d in details:
                if isinstance(d, (str, int, float)):
                    s = str(d).strip()
                    if s:
                        vals.append(s)
        WORLD.scene_details = vals
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


def set_speed(name: str, value: float = DEFAULT_MOVE_SPEED_STEPS, unit: str = "steps"):
    """Set walking speed for an actor (steps only by default).

    Args:
        name: Actor identifier.
        value: Numeric speed value.
        unit: 'steps' (default) or 'feet'.
    """

    unit_norm = (unit or "steps").lower()
    if unit_norm not in {"steps", "feet"}:
        unit_norm = "steps"

    if unit_norm == "feet":
        # Assume standard 5ft per grid step
        steps = max(0, int(math.ceil(float(value) / 5.0)))
    else:
        steps = max(0, int(round(float(value))))

    if steps == 0 and value > 0:
        steps = 1
    WORLD.speeds[str(name)] = steps
    return ToolResponse(
        content=[TextBlock(type="text", text=f"速度设定：{name} {format_distance_steps(steps)}")],
        metadata={"name": name, "speed_steps": steps},
    )


def roll_initiative(participants: Optional[List[str]] = None):
    names = list(participants or list(WORLD.characters.keys()))
    # Remove any downed participants up-front so turns never start on a dead unit
    names = [n for n in names if _is_alive(n)]
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
    # reset tokens for first actor (if any)
    first = _current_actor_name()
    if first:
        _reset_turn_tokens_for(first)
    txt = "先攻：" + ", ".join(f"{n}({scores[n]})" for n in ordered)
    return ToolResponse(content=[TextBlock(type="text", text=txt)], metadata={"initiative": ordered, "scores": scores})



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


def _is_alive(name: Optional[str]) -> bool:
    """Return True if the character is alive (hp>0).

    A missing sheet is treated as alive to avoid accidental soft-locks.
    """
    if not name:
        return False
    try:
        st = WORLD.characters.get(str(name), {})
        hp = int(st.get("hp", 1))
        return hp > 0
    except Exception:
        # Be permissive; if we cannot determine, assume alive
        return True


def _reset_turn_tokens_for(name: Optional[str]):
    if not name:
        return
    spd = int(WORLD.speeds.get(name, _default_move_steps()))
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
    """Advance to the next alive actor and start their turn.

    - Skips over any actors whose HP<=0.
    - Increments round when wrapping from the end to the start.
    - If no alive actors exist, preserves indices and reports accordingly.
    """
    if not WORLD.in_combat or not WORLD.initiative_order:
        return ToolResponse(content=[TextBlock(type="text", text="未处于战斗中")], metadata={"in_combat": False})

    order = WORLD.initiative_order
    if not order:
        return ToolResponse(content=[TextBlock(type="text", text="未处于战斗中")], metadata={"in_combat": False})

    prev_idx = int(WORLD.turn_idx)
    n = len(order)

    # Search for the next alive actor within one full cycle
    chosen_idx: Optional[int] = None
    wrapped = False
    for step in range(1, n + 1):
        idx = (prev_idx + step) % n
        if idx <= prev_idx:
            wrapped = True
        cand = order[idx]
        if _is_alive(cand):
            chosen_idx = idx
            break

    if chosen_idx is None:
        # No alive participants; nothing to do
        note = TextBlock(type="text", text="[系统] 无可行动单位（全部倒地或未登记）")
        return ToolResponse(content=[note], metadata={"round": WORLD.round, "actor": None, "ok": False})

    WORLD.turn_idx = chosen_idx
    if wrapped:
        WORLD.round += 1

    cur = order[WORLD.turn_idx]
    _reset_turn_tokens_for(cur)
    return ToolResponse(
        content=[TextBlock(type="text", text=f"回合推进：R{WORLD.round} 轮到 {cur}")],
        metadata={"round": WORLD.round, "actor": cur, "ok": True},
    )


def get_turn() -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=f"当前：R{WORLD.round} idx={WORLD.turn_idx} actor={_current_actor_name() or '(未定)'}")], metadata={
        "round": WORLD.round,
        "turn_idx": WORLD.turn_idx,
        "actor": _current_actor_name(),
        "order": list(WORLD.initiative_order),
        "state": dict(WORLD.turn_state.get(_current_actor_name() or "", {})),
    })


def reset_actor_turn(name: str) -> ToolResponse:
    """Reset per-turn tokens for the given actor, regardless of combat mode.

    This aligns the per-回合资源（移动/动作/反应）与 Host 的普通轮转一致，
    不再依赖战斗状态或先攻顺序。
    """
    nm = str(name)
    _reset_turn_tokens_for(nm)
    st = dict(WORLD.turn_state.get(nm, {}))
    return ToolResponse(
        content=[TextBlock(type="text", text=f"[系统] {nm} 回合资源重置")],
        metadata={"name": nm, "state": st},
    )


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


def consume_movement(name: str, distance_steps: float) -> ToolResponse:
    """Spend movement measured in grid steps."""

    nm = str(name)
    st = WORLD.turn_state.setdefault(nm, {})
    default_steps = int(WORLD.speeds.get(nm, _default_move_steps()))
    left = int(st.get("move_left", default_steps))
    steps = int(math.ceil(max(0.0, float(distance_steps))))
    if steps <= 0:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"{nm} 不移动")],
            metadata={"ok": True, "left_steps": left},
        )
    if steps > left:
        st["move_left"] = 0
        return ToolResponse(
            content=[TextBlock(type="text", text=f"{nm} 试图移动 {format_distance_steps(steps)}，但仅剩 {format_distance_steps(left)}；按剩余移动结算")],
            metadata={"ok": False, "left_steps": 0, "attempted_steps": steps},
        )
    st["move_left"] = left - steps
    return ToolResponse(
        content=[TextBlock(type="text", text=f"{nm} 移动 {format_distance_steps(steps)}（剩余 {format_distance_steps(st['move_left'])}）")],
        metadata={"ok": True, "left_steps": st["move_left"], "spent_steps": steps},
    )


def auto_move_into_reach(attacker: str, defender: str, reach_steps: Optional[int] = None) -> ToolResponse:
    """Attempt to move attacker into melee reach of defender."""

    atk = str(attacker)
    dfd = str(defender)
    reach = max(1, int(reach_steps if reach_steps is not None else get_reach_steps(atk)))
    distance_before = get_distance_steps_between(atk, dfd)
    out: List[TextBlock] = []

    if distance_before is None:
        out.append(TextBlock(type="text", text=f"{atk} 未知与 {dfd} 的距离，无法自动靠近。"))
        return ToolResponse(content=out, metadata={"ok": False, "reason": "distance_unknown", "reach_steps": reach})

    if distance_before <= reach:
        out.append(TextBlock(type="text", text=f"{atk} 已处于攻击距离内（距离 {format_distance_steps(distance_before)}）。"))
        return ToolResponse(content=out, metadata={"ok": True, "moved_steps": 0, "distance_before": distance_before, "distance_after": distance_before, "reach_steps": reach})

    target_pos = WORLD.positions.get(dfd)
    if target_pos is None:
        out.append(TextBlock(type="text", text=f"尚未记录 {dfd} 的坐标，无法靠近。"))
        return ToolResponse(content=out, metadata={"ok": False, "reason": "target_position_missing", "reach_steps": reach})

    need = distance_before - reach
    turn_state = WORLD.turn_state.setdefault(atk, {})
    left = int(turn_state.get("move_left", get_move_speed_steps(atk)))
    if left <= 0:
        out.append(TextBlock(type="text", text=f"{atk} 移动力耗尽，仍距 {dfd} {format_distance_steps(distance_before)}。"))
        return ToolResponse(content=out, metadata={"ok": False, "reason": "no_movement", "needed_steps": need, "distance_before": distance_before, "reach_steps": reach})

    move_steps = min(need, left)
    consume_res = consume_movement(atk, move_steps)
    move_res = move_towards(atk, target_pos, move_steps)
    out.extend(consume_res.content or [])
    out.extend(move_res.content or [])

    distance_after = get_distance_steps_between(atk, dfd)
    in_reach = distance_after is not None and distance_after <= reach
    if not in_reach and distance_after is not None:
        out.append(TextBlock(type="text", text=f"仍距 {dfd} {format_distance_steps(distance_after)}，触及范围 {format_distance_steps(reach)}。"))
    elif distance_after is None:
        out.append(TextBlock(type="text", text=f"无法确认与 {dfd} 的最终距离。"))

    meta = {
        "ok": bool(in_reach),
        "distance_before": distance_before,
        "distance_after": distance_after,
        "reach_steps": reach,
        "needed_steps": need,
        "moved_steps": move_res.metadata.get("moved") if move_res.metadata else move_steps,
        "movement": consume_res.metadata,
    }
    return ToolResponse(content=out, metadata=meta)


# ---- Range bands & cover/conditions ----
BANDS = ["engaged", "near", "far", "long"]


def set_range_band(a: str, b: str, band: str):
    band = str(band)
    if band not in BANDS:
        return ToolResponse(content=[TextBlock(type="text", text=f"未知距离带 {band}")], metadata={"ok": False})
    k = _pair_key(a, b)
    WORLD.range_bands[k] = band
    return ToolResponse(content=[TextBlock(type="text", text=f"距离：{k[0]}↔{k[1]} = {band}")], metadata={"ok": True, "pair": list(k), "band": band})


def get_range_band(a: str, b: str) -> str:
    return WORLD.range_bands.get(_pair_key(a, b), "near")


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
            cost += 1
        elif (a, b) == ("near", "far"):
            cost += 6
        else:
            cost += 12
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
    spd_steps = int(WORLD.speeds.get(nm, _default_move_steps()))
    st["move_left"] = int(st.get("move_left", spd_steps)) + spd_steps
    return ToolResponse(
        content=[TextBlock(type="text", text=f"{nm} 冲刺（移动力+{format_distance_steps(spd_steps)}）")],
        metadata={"ok": True, "move_left_steps": st["move_left"]}
    )


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
    move_speed_steps: Optional[int] = None,
    reach_steps: Optional[int] = None,
    *,
    # Backward-compat: accept legacy alias `move_speed` (steps)
    move_speed: Optional[int] = None,
) -> ToolResponse:
    """Create/update a D&D-style character sheet (simplified, steps-only distances).

    Distances and speeds are stored and displayed in grid steps（步）。
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
    # Movement (steps per turn). Prefer explicit steps; fall back to legacy alias `move_speed`.
    ms = (
        move_speed_steps
        if move_speed_steps is not None
        else (move_speed if move_speed is not None else sheet.get("move_speed_steps", DEFAULT_MOVE_SPEED_STEPS))
    )
    try:
        move_steps = int(ms)
    except Exception:
        move_steps = int(DEFAULT_MOVE_SPEED_STEPS)
    if move_steps <= 0:
        move_steps = 1
    sheet["move_speed_steps"] = move_steps
    WORLD.speeds[name] = move_steps

    # Reach (steps)
    rs = reach_steps if reach_steps is not None else sheet.get("reach_steps", sheet.get("reach", DEFAULT_REACH_STEPS))
    try:
        rsteps = int(rs)
    except Exception:
        rsteps = int(DEFAULT_REACH_STEPS)
    rsteps = max(1, rsteps)
    sheet["reach_steps"] = rsteps

    # Keep legacy keys for compatibility
    WORLD.characters[name]["hp"] = sheet["hp"]
    WORLD.characters[name]["max_hp"] = sheet["max_hp"]
    return ToolResponse(
        content=[TextBlock(type="text", text=f"设定 {name}（Lv{sheet['level']} AC {sheet['ac']} HP {sheet['hp']}/{sheet['max_hp']}，移动 {format_distance_steps(move_steps)}，触及 {format_distance_steps(rsteps)}）")],
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
    auto_move: bool = False,
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

    def _fmt_distance(steps: Optional[int]) -> str:
        if steps is None:
            return "未知"
        return format_distance_steps(int(steps))

    reach_steps = get_reach_steps(attacker)
    distance_before = get_distance_steps_between(attacker, defender)
    distance_after = distance_before
    pre_logs: List[TextBlock] = []
    auto_meta: Optional[Dict[str, Any]] = None

    if distance_before is not None and distance_before > reach_steps:
        if auto_move:
            move_res = auto_move_into_reach(attacker, defender, reach_steps)
            pre_logs.extend(move_res.content or [])
            auto_meta = move_res.metadata or {}
            distance_after = get_distance_steps_between(attacker, defender)
        else:
            pre_logs.append(
                TextBlock(
                    type="text",
                    text=f"距离不足：{attacker} 与 {defender} 相距 {_fmt_distance(distance_before)}，触及范围 {_fmt_distance(reach_steps)}。",
                )
            )
            return ToolResponse(
                content=pre_logs,
                metadata={
                    "attacker": attacker,
                    "defender": defender,
                    "hit": False,
                    "reach_ok": False,
                    "distance_before": distance_before,
                    "distance_after": distance_before,
                    "reach_steps": reach_steps,
                    "auto_move": auto_meta,
                },
            )

    if distance_after is not None and distance_after > reach_steps:
        pre_logs.append(
            TextBlock(
                type="text",
                text=f"{attacker} 与 {defender} 仍未进入触及范围（当前距离 {_fmt_distance(distance_after)}，触及 {_fmt_distance(reach_steps)}）。",
            )
        )
        return ToolResponse(
            content=pre_logs,
            metadata={
                "attacker": attacker,
                "defender": defender,
                "hit": False,
                "reach_ok": False,
                "distance_before": distance_before,
                "distance_after": distance_after,
                "reach_steps": reach_steps,
                "auto_move": auto_meta,
            },
        )

    # Attack roll
    atk_res = skill_check(target=int(ac), modifier=base, advantage=advantage)
    success = bool(atk_res.metadata.get("success")) if atk_res.metadata else False
    parts: List[TextBlock] = list(pre_logs)
    parts.append(TextBlock(type="text", text=f"攻击：{attacker} -> {defender} d20{_signed(base)} vs AC {ac} -> {'命中' if success else '未中'}"))
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
        "reach_ok": True,
        "distance_before": distance_before,
        "distance_after": distance_after,
        "reach_steps": reach_steps,
        "auto_move": auto_meta,
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
                    # Support absolute target (value/target) with delta fallback for compatibility
                    a, b = eff.get("a"), eff.get("b")
                    if a and b:
                        if ("value" in eff) or ("target" in eff):
                            v = eff.get("value", eff.get("target", 0))
                            set_relation(str(a), str(b), int(v), reason=str(eff.get("reason", "")))
                        else:
                            d = int(eff.get("delta", 0))
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
