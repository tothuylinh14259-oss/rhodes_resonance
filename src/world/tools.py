# Minimal world state and tools for the demo; designed to be pure and easy to test.
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Tuple, Any, List, Optional
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

    def snapshot(self) -> dict:
        return {
            "time_min": self.time_min,
            "weather": self.weather,
            "relations": {f"{a}&{b}": v for (a, b), v in self.relations.items()},
            "inventory": self.inventory,
            "characters": self.characters,
            "location": self.location,
            "objectives": list(self.objectives),
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

    # Scene & objectives
    loc = snap.get("location", "未知")
    objs = snap.get("objectives", []) or []
    obj_line = "; ".join(str(o) for o in objs) if objs else "(无)"

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
        f"地点：{loc}",
        f"目标：{obj_line}",
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


def set_scene(location: str, objectives: Optional[List[str]] = None, append: bool = False):
    """Set the current scene location and optionally objectives list.

    Args:
        location: 新地点描述
        objectives: 目标列表；append=True 时为追加，否则替换
        append: 是否在现有目标后追加
    """
    WORLD.location = str(location)
    if objectives is not None:
        if append:
            WORLD.objectives.extend(list(objectives))
        else:
            WORLD.objectives = list(objectives)
    text = f"设定场景：{WORLD.location}；目标：{'; '.join(WORLD.objectives) if WORLD.objectives else '(无)'}"
    return ToolResponse(content=[TextBlock(type="text", text=text)], metadata=WORLD.snapshot())


def add_objective(obj: str):
    """Append a single objective into the world's objectives list."""
    WORLD.objectives.append(str(obj))
    text = f"新增目标：{obj}"
    return ToolResponse(content=[TextBlock(type="text", text=text)], metadata={"objectives": list(WORLD.objectives)})


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
    if success:
        # Replace ability placeholders in damage expr
        dmg_expr2 = _replace_ability_tokens(damage_expr, mod)
        dmg_res = roll_dice(dmg_expr2)
        total = int(dmg_res.metadata.get("total", 0)) if dmg_res.metadata else 0
        dmg_apply = damage(defender, total)
        parts.append(TextBlock(type="text", text=f"伤害：{dmg_expr2} -> {total}"))
        for blk in dmg_apply.content or []:
            if blk.get("type") == "text":
                parts.append(blk)
    return ToolResponse(content=parts, metadata={"attacker": attacker, "defender": defender, "hit": success, "base": base})


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
