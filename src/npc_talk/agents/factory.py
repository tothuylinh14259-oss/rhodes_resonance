from __future__ import annotations

import logging
import os
from typing import Optional

from agentscope.agent import ReActAgent  # type: ignore
from agentscope.formatter import OpenAIChatFormatter  # type: ignore
from agentscope.memory import InMemoryMemory  # type: ignore
from agentscope.model import OpenAIChatModel  # type: ignore
from agentscope.tool import Toolkit  # type: ignore

from npc_talk.config import ModelConfig
from npc_talk.world.tools import (
    describe_world,
    attack_roll_dnd,
    move_towards,
    skill_check_dnd,
    change_relation,
    grant_item,
)


_ACTION_LOGGER = logging.getLogger("npc_talk_demo")


def _log_action(msg: str) -> None:
    try:
        if not msg:
            return
        _ACTION_LOGGER.info(f"[ACTION] {msg}")
    except Exception:
        pass


def perform_attack(attacker, defender, ability="STR", proficient=False, target_ac=None, damage_expr="1d4+STR", advantage="none"):
    """Wrapper for attack_roll_dnd with simpler schema."""
    resp = attack_roll_dnd(
        attacker=attacker,
        defender=defender,
        ability=ability,
        proficient=proficient,
        target_ac=target_ac,
        damage_expr=damage_expr,
        advantage=advantage,
    )
    meta = resp.metadata or {}
    hit = meta.get("hit")
    dmg = meta.get("damage_total")
    hp_before = meta.get("hp_before")
    hp_after = meta.get("hp_after")
    _log_action(
        f"attack {attacker} -> {defender} | hit={hit} dmg={dmg} hp:{hp_before}->{hp_after}"
    )
    return resp


def perform_skill_check(name, skill, dc, advantage="none"):
    """Wrapper for skill_check_dnd."""
    resp = skill_check_dnd(name=name, skill=skill, dc=dc, advantage=advantage)
    meta = resp.metadata or {}
    success = meta.get("success")
    total = meta.get("total")
    roll = meta.get("roll")
    _log_action(
        f"skill_check {name} skill={skill} dc={dc} -> success={success} total={total} roll={roll}"
    )
    return resp


def advance_position(name, target, steps):
    """Wrapper for move_towards accepting list/tuple targets."""
    if isinstance(target, dict):
        tx = target.get("x", 0)
        ty = target.get("y", 0)
        tgt = (int(tx), int(ty))
    elif isinstance(target, (list, tuple)) and len(target) >= 2:
        tgt = (int(target[0]), int(target[1]))
    else:
        tgt = (0, 0)
    resp = move_towards(name=name, target=tgt, steps=int(steps))
    meta = resp.metadata or {}
    _log_action(
        f"move {name} -> {tgt} steps={steps} moved={meta.get('moved')} remaining={meta.get('remaining')}"
    )
    return resp


def adjust_relation(a, b, delta, reason=""):
    """Wrapper for change_relation with simple schema."""
    resp = change_relation(a=a, b=b, delta=int(delta), reason=reason)
    meta = resp.metadata or {}
    _log_action(
        f"relation {a}<->{b} delta={delta} score={meta.get('score')} reason={reason or '无'}"
    )
    return resp


def transfer_item(target, item, n=1):
    """Wrapper for grant_item."""
    resp = grant_item(target=target, item=item, n=int(n))
    meta = resp.metadata or {}
    _log_action(
        f"transfer item={item} -> {target} qty={n} total={meta.get('count')}"
    )
    return resp


TOOL_DISPATCH = {
    "describe_world": describe_world,
    "perform_attack": perform_attack,
    "perform_skill_check": perform_skill_check,
    "advance_position": advance_position,
    "adjust_relation": adjust_relation,
    "transfer_item": transfer_item,
}


DEFAULT_INTENT_SCHEMA = (
    '{\n  "intent": "attack|talk|investigate|move|assist|use_item|skill_check|wait",\n'
    '  "target": "目标名称",\n  "skill": "perception|medicine|...",\n'
    '  "ability": "STR|DEX|CON|INT|WIS|CHA",\n  "proficient": true,\n  "dc_hint": 12,\n'
    '  "damage_expr": "1d4+STR",\n  "time_cost": 1\n}'
)

DEFAULT_PROMPT_HEADER = "你是游戏中的NPC：{name}。人设：{persona}。\n"
DEFAULT_PROMPT_RULES = (
    "对话要求：\n"
    "- 先用简短中文说1-2句对白/想法/微动作，符合人设。\n"
    "- 若需要了解环境信息或关系，请调用 describe_world()。\n"
    "- 当需要执行行动时，直接调用工具（格式：CALL_TOOL tool_name({{\"key\": \"value\"}}))，不要再输出意图 JSON。\n"
    "- 调用工具后等待系统反馈，再根据结果做简短评论或继续对白。\n"
    "- 阅读主持人消息或 describe_world() 中的‘关系’提示，根据亲疏调整语气与目标选择。\n"
    "- 针对其他角色采取行动前，必须先确认双方关系分数：≥40 视为亲密同伴（避免攻击、优先支援），≥10 为盟友（若要伤害需先说明理由），≤-10 才视为敌方目标。\n"
    "- 最近两条消息内若未确认关系，请先调用 describe_world(detail=True) 后再决定行动。\n"
    "- 参与者名称（仅可用）：{allowed_names}\n"
)

DEFAULT_PROMPT_TOOL_GUIDE = (
    "可用工具：\n"
    "- describe_world(detail: bool = False)：获取当前环境、关系、坐标。\n"
    "- perform_attack(attacker, defender, ability='STR', proficient=False, target_ac=None, damage_expr='1d4+STR', advantage='none')：发动攻击并自动结算伤害。\n"
    "- perform_skill_check(name, skill, dc, advantage='none')：执行技能检定。\n"
    "- advance_position(name, target:[x,y], steps:int)：朝指定坐标逐步接近。\n"
    "- adjust_relation(a, b, delta, reason='')：在合适情境下调整关系。\n"
    "- transfer_item(target, item, n=1)：移交或分配物资。\n"
)

DEFAULT_PROMPT_EXAMPLE = (
    "输出示例：\n"
    "阿米娅瞥向敌对的械徒，低声提醒博士：‘他已经与我们敌对，只能先压制。’\n"
    'CALL_TOOL describe_world({{"detail": true}})\n'
    'CALL_TOOL perform_attack({{"attacker":"Amiya","defender":"Enemy","ability":"DEX","damage_expr":"2d6+DEX"}})\n'
)

DEFAULT_PROMPT_TEMPLATE = (
    DEFAULT_PROMPT_HEADER + DEFAULT_PROMPT_RULES + DEFAULT_PROMPT_TOOL_GUIDE + DEFAULT_PROMPT_EXAMPLE
)


def _join_lines(tpl):
    if isinstance(tpl, list):
        try:
            return "\n".join(str(x) for x in tpl)
        except Exception:
            return "\n".join(tpl)
    return tpl


def make_kimi_npc(
    name: str,
    persona: str,
    model_cfg: ModelConfig,
    prompt_template: Optional[str | list[str]] = None,
    allowed_names: Optional[str] = None,
) -> ReActAgent:
    """Create an LLM-backed NPC using Kimi's OpenAI-compatible API."""
    api_key = os.environ["MOONSHOT_API_KEY"]
    base_url = model_cfg.base_url or os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    sec = model_cfg.npc or {}
    model_name = sec.get("model") or os.getenv("KIMI_MODEL", "kimi-k2-turbo-preview")

    # Build system prompt
    tools = "describe_world()"
    intent_schema = DEFAULT_INTENT_SCHEMA
    tpl = _join_lines(prompt_template)
    sys_prompt = None
    if tpl:
        try:
            sys_prompt = tpl.format(
                name=name,
                persona=persona,
                tools=tools,
                intent_schema=intent_schema,
                allowed_names=(allowed_names or "Doctor, Amiya"),
            )
        except Exception:
            sys_prompt = None
    if not sys_prompt:
        sys_prompt = DEFAULT_PROMPT_TEMPLATE.format(
            name=name,
            persona=persona,
            allowed_names=allowed_names or "Doctor, Amiya",
            intent_schema=intent_schema,
        )

    model = OpenAIChatModel(
        model_name=model_name,
        api_key=api_key,
        stream=bool(sec.get("stream", True)),
        client_args={"base_url": base_url},
        generate_kwargs={"temperature": float(sec.get("temperature", 0.7))},
    )

    toolkit = Toolkit()
    toolkit.register_tool_function(describe_world)
    toolkit.register_tool_function(perform_attack)
    toolkit.register_tool_function(perform_skill_check)
    toolkit.register_tool_function(advance_position)
    toolkit.register_tool_function(adjust_relation)
    toolkit.register_tool_function(transfer_item)

    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=model,
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
        toolkit=toolkit,
    )
