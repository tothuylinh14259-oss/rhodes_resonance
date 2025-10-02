# Minimal world state and tools for the demo; designed to be pure and easy to test.
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Tuple, Any, List
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

    def snapshot(self) -> dict:
        return {
            "time_min": self.time_min,
            "weather": self.weather,
            "relations": {f"{a}&{b}": v for (a, b), v in self.relations.items()},
            "inventory": self.inventory,
            "characters": self.characters,
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
    return ToolResponse(
        content=[TextBlock(type="text", text=f"时间推进 {int(mins)} 分钟，当前时间(分钟)={WORLD.time_min}")],
        metadata=res,
    )


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

    lines = [
        f"时间：{time_str}",
        f"天气：{weather}",
        ("关系：" + "; ".join(rel_lines)) if rel_lines else "关系：无变动",
        ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
        ("角色：" + "; ".join(char_lines)) if char_lines else "角色：未登记",
    ]
    if detail:
        lines.append("(详情见元数据)")

    text = "\n".join(lines)
    return ToolResponse(content=[TextBlock(type="text", text=text)], metadata=snap)


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
