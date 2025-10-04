import random

from npc_talk.world.tools import (
    WORLD,
    set_dnd_character,
    attack_roll_dnd,
    skill_check_dnd,
    roll_dice,
    set_position,
    get_position,
)


def test_set_and_get_position():
    res1 = set_position("Tester", 2, 3)
    meta = res1.metadata or {}
    assert meta.get("position") == [2, 3]
    res2 = get_position("Tester")
    assert res2.metadata.get("position") == [2, 3]


def test_stat_block_and_attack():
    # Deterministic randomness
    random.seed(42)
    set_dnd_character(
        name="A",
        level=1,
        ac=12,
        abilities={"STR": 12, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        max_hp=10,
        proficient_skills=["athletics"],
        proficient_saves=["STR"],
        move_speed=6,
    )
    set_dnd_character(
        name="B",
        level=1,
        ac=10,
        abilities={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        max_hp=10,
    )
    res = attack_roll_dnd("A", "B", ability="STR", proficient=True, damage_expr="1d4+STR")
    assert "攻击" in (res.content or [{}])[0].get("text", "")
    # hp should be <= max after damage applied
    hp_after = WORLD.characters["B"]["hp"]
    assert 0 <= hp_after <= WORLD.characters["B"]["max_hp"]


def test_roll_dice_parse_and_total():
    random.seed(123)
    out = roll_dice("2d6+1")
    total = out.metadata.get("total")
    assert isinstance(total, int)
    assert 3 <= total <= 13

