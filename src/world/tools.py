# Minimal world state and tools for the demo; designed to be pure and easy to test.
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Tuple


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
    WORLD.time_min += int(mins)
    return {"ok": True, "time_min": WORLD.time_min}


def change_relation(a: str, b: str, delta: int, reason: str = ""):
    k = _rel_key(a, b)
    WORLD.relations[k] = WORLD.relations.get(k, 0) + int(delta)
    return {"ok": True, "pair": list(k), "score": WORLD.relations[k], "reason": reason}


def grant_item(target: str, item: str, n: int = 1):
    bag = WORLD.inventory.setdefault(target, {})
    bag[item] = bag.get(item, 0) + int(n)
    return {"ok": True, "target": target, "item": item, "count": bag[item]}
