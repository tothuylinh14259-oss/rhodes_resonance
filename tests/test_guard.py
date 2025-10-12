import random

from world.tools import (
    WORLD,
    set_dnd_character,
    set_position,
    set_weapon_defs,
    grant_item,
    attack_with_weapon,
    use_action,
    set_guard,
    clear_guard,
)


def setup_scene_basic():
    # Clear world bits that may interfere
    WORLD.characters.clear()
    WORLD.positions.clear()
    WORLD.inventory.clear()
    WORLD.guardians.clear()
    WORLD.turn_state.clear()
    WORLD.weapon_defs.clear()


def test_guard_redirects_target():
    random.seed(7)
    setup_scene_basic()
    # Protector A, Protectee B, Attacker C
    set_dnd_character(name="A", level=1, ac=12, abilities={"STR": 12, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=12)
    set_dnd_character(name="B", level=1, ac=10, abilities={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=10)
    set_dnd_character(name="C", level=1, ac=10, abilities={"STR": 14, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=10)
    set_position("B", 0, 0)
    set_position("A", 1, 0)  # adjacent to B
    set_position("C", 1, 1)  # within 1 step to A and B
    set_guard("A", "B")

    set_weapon_defs({
        "longsword": {"reach_steps": 1, "ability": "STR", "damage_expr": "1d8+STR", "proficient_default": True}
    })
    grant_item("C", "longsword", 1)
    res = attack_with_weapon("C", "B", weapon="longsword")
    # Defender should be redirected to A
    assert res.metadata.get("defender") == "A"
    guard = (res.metadata or {}).get("guard", {})
    assert guard.get("protector") == "A" and guard.get("protected") == "B"


def test_guard_requires_reaction():
    random.seed(8)
    setup_scene_basic()
    set_dnd_character(name="A", level=1, ac=12, abilities={"STR": 12, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=12)
    set_dnd_character(name="B", level=1, ac=10, abilities={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=10)
    set_dnd_character(name="C", level=1, ac=10, abilities={"STR": 14, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=10)
    set_position("B", 0, 0)
    set_position("A", 1, 0)
    set_position("C", 1, 1)
    set_guard("A", "B")
    # Spend A's reaction beforehand
    use_action("A", "reaction")

    set_weapon_defs({"mace": {"reach_steps": 1, "ability": "STR", "damage_expr": "1d6+STR", "proficient_default": True}})
    grant_item("C", "mace", 1)
    res = attack_with_weapon("C", "B", weapon="mace")
    # No redirection due to lack of reaction
    assert res.metadata.get("defender") == "B"
    assert (res.metadata or {}).get("guard") is None


def test_guard_requires_proximity():
    random.seed(9)
    setup_scene_basic()
    set_dnd_character(name="A", level=1, ac=12, abilities={"STR": 12, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=12)
    set_dnd_character(name="B", level=1, ac=10, abilities={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=10)
    set_dnd_character(name="C", level=1, ac=10, abilities={"STR": 14, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=10)
    set_position("B", 0, 0)
    set_position("A", 2, 0)  # not adjacent (distance=2)
    set_position("C", 1, 1)
    set_guard("A", "B")

    set_weapon_defs({"club": {"reach_steps": 1, "ability": "STR", "damage_expr": "1d4+STR", "proficient_default": True}})
    grant_item("C", "club", 1)
    res = attack_with_weapon("C", "B", weapon="club")
    # No redirection due to non-adjacency
    assert res.metadata.get("defender") == "B"
    assert (res.metadata or {}).get("guard") is None


def test_multiple_guardians_priority_nearest_to_attacker():
    random.seed(10)
    setup_scene_basic()
    set_dnd_character(name="A", level=1, ac=12, abilities={"STR": 12, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=12)
    set_dnd_character(name="D", level=1, ac=12, abilities={"STR": 12, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=12)
    set_dnd_character(name="B", level=1, ac=10, abilities={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=10)
    set_dnd_character(name="C", level=1, ac=10, abilities={"STR": 10, "DEX": 16, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10}, max_hp=10)
    set_position("B", 0, 0)
    set_position("A", 1, 0)  # adjacent
    set_position("D", 0, 1)  # adjacent
    set_position("C", 10, 0)
    set_guard("A", "B")
    set_guard("D", "B")

    set_weapon_defs({
        "bow": {"reach_steps": 12, "ability": "DEX", "damage_expr": "1d8+DEX", "proficient_default": True}
    })
    grant_item("C", "bow", 1)
    res = attack_with_weapon("C", "B", weapon="bow")
    # C->A distance 9, C->D distance 10 -> choose A
    assert res.metadata.get("defender") == "A"
    assert (res.metadata or {}).get("guard", {}).get("protector") == "A"

