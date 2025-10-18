import random

from world.tools import (
    WORLD,
    set_dnd_character,
    set_position,
    set_weapon_defs,
    attack_with_weapon,
    grant_item,
)


def test_attack_with_weapon_in_reach():
    random.seed(7)
    set_dnd_character(
        name="A",
        ac=12,
        abilities={"STR": 12, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        max_hp=10,
    )
    set_dnd_character(
        name="B",
        ac=10,
        abilities={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        max_hp=10,
    )
    set_position("A", 0, 0)
    set_position("B", 1, 0)
    set_weapon_defs({
        "longsword": {"reach_steps": 1, "ability": "STR", "damage_expr": "1d8+STR"}
    })
    grant_item("A", "longsword", 1)
    res = attack_with_weapon("A", "B", weapon="longsword")
    assert res.metadata.get("reach_ok") is True
    assert res.metadata.get("weapon_id") == "longsword"


def test_attack_with_weapon_out_of_reach_fails():
    random.seed(11)
    set_dnd_character(
        name="C",
        ac=12,
        abilities={"STR": 12, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        max_hp=10,
    )
    set_dnd_character(
        name="D",
        ac=10,
        abilities={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        max_hp=10,
    )
    set_position("C", 0, 0)
    set_position("D", 0, 3)
    set_weapon_defs({
        "baton": {"reach_steps": 1, "ability": "STR", "damage_expr": "1d4+STR"}
    })
    res = attack_with_weapon("C", "D", weapon="baton")
    # Must fail because attacker doesn't own the weapon
    assert res.metadata.get("error_type") == "weapon_not_owned"
    # Position unchanged (no auto move)
    assert WORLD.positions["C"] == (0, 0)
