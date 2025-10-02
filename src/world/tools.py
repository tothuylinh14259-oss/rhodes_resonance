# Minimal world state and tools for the demo; designed to be pure and easy to test.
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Tuple
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

    def snapshot(self) -> dict:
        return {
            "time_min": self.time_min,
            "weather": self.weather,
            "relations": {f"{a}&{b}": v for (a, b), v in self.relations.items()},
            "inventory": self.inventory,
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

    lines = [
        f"时间：{time_str}",
        f"天气：{weather}",
        ("关系：" + "; ".join(rel_lines)) if rel_lines else "关系：无变动",
        ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
    ]
    if detail:
        lines.append("(详情见元数据)")

    text = "\n".join(lines)
    return ToolResponse(content=[TextBlock(type="text", text=text)], metadata=snap)
