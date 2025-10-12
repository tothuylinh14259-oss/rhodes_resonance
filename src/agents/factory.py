from __future__ import annotations

import os
from typing import Iterable, Optional, Mapping, Any

from agentscope.agent import ReActAgent  # type: ignore
from agentscope.formatter import OpenAIChatFormatter  # type: ignore
from agentscope.memory import InMemoryMemory  # type: ignore
from agentscope.model import OpenAIChatModel  # type: ignore
from agentscope.tool import Toolkit  # type: ignore

# No component imports; the model configuration is passed in from main as a mapping.


DEFAULT_PROMPT_HEADER = (
    "你是游戏中的NPC：{name}。\n"
    "人设：{persona}\n"
    "外观特征：{appearance}\n"
    "常用语气/台词：{quotes}\n"
    "当前立场提示（仅你视角）：{relation_brief}\n"
    "可用武器：{weapon_brief}\n"
)
DEFAULT_PROMPT_RULES = (
    "对话要求：\n"
    "- 先用中文说1-2句对白/想法/微动作，符合人设。\n"
    "- 当需要执行行动时，直接调用工具（格式：CALL_TOOL tool_name({{\"key\": \"value\"}}))，不要再输出意图 JSON。\n"
    "- 调用工具后等待系统反馈，再根据结果做简短评论或继续对白。\n"
    "- 行动前对照上方立场提示：≥40 视为亲密同伴（避免攻击、优先支援），≥10 为盟友（若要伤害需先说明理由），≤-10 才视为敌方目标，其余保持谨慎中立。\n"
    "- 若必须违背既定关系行事，若要违背，请在对白中说明充分理由，否则拒绝执行。\n"
    "- 每次调用工具，JSON 中必须包含 reason 字段，用一句话说明行动理由；若缺省系统将记录为‘未提供’。\n"
    # 强化约束：避免“系统提示”式旁白，并在有敌对关系时强制采取行动
    "- 当存在敌对关系（关系<=-10）时，每回合至少调用一次工具；否则视为违规。\n"
    "- 不要输出任何“系统提示”或括号内的系统旁白；只输出对白与 CALL_TOOL。\n"
    "- 参与者名称（仅可用）：{allowed_names}\n"
)

DEFAULT_PROMPT_TOOL_GUIDE = (
    "可用工具：\n"
    "- perform_attack(attacker, defender, weapon, reason)：使用指定武器发起攻击（触及范围与伤害来自武器定义）；必须提供行动理由（reason）。攻击不会自动靠近，若距离不足请先调用 advance_position()。\n"
    "- advance_position(name, target:[x,y], steps:int, reason)：朝指定坐标逐步接近；必须提供行动理由。\n"
    "- adjust_relation(a, b, value, reason)：在合适情境下将关系直接设为目标值（已内置理由记录）。\n"
    "- transfer_item(target, item, n=1, reason)：移交或分配物资；必须提供行动理由。\n"
    "- set_protection(guardian, protectee, reason)：建立守护关系（guardian 将在相邻且有反应时替代 protectee 承受攻击）。\n"
    "- clear_protection(guardian=\"\", protectee=\"\", reason)：清除守护关系；可按守护者/被保护者/全部清理。\n"
)

DEFAULT_PROMPT_EXAMPLE = (
    "输出示例：\n"
    "阿米娅压低声音：‘靠近目标位置。’\n"
    'CALL_TOOL advance_position({{"name": "Amiya", "target": {{"x": 1, "y": 1}}, "steps": 2, "reason": "接近掩体"}})\n'
)

# Additional guidance so NPCs understand how protection actually takes effect
DEFAULT_PROMPT_GUARD_GUIDE = (
    "守护生效规则：\n"
    "- set_protection 仅建立关系；要触发拦截，guardian 必须与 protectee 相邻（≤1步），且 guardian 本轮有可用‘反应’。\n"
    "- 攻击者到 guardian 的距离也必须在本次武器触及/射程内，否则无法替代承伤。\n"
    "- 多名守护者同时满足时，系统选择距离攻击者最近者（同距按登记顺序）。\n"
    "- 建议建立守护后使用 advance_position 贴身到被保护者旁并保持相邻，以确保拦截能生效。\n"
)

DEFAULT_PROMPT_GUARD_EXAMPLE = (
    "守护使用示例：\n"
    "德克萨斯侧身一步：‘我来护你。’\n"
    'CALL_TOOL set_protection({{"guardian": "Texas", "protectee": "Amiya", "reason": "建立守护"}})\n'
    "德克萨斯快步靠近：\n"
    'CALL_TOOL advance_position({{"name": "Texas", "target": {{"x": 1, "y": 1}}, "steps": 1, "reason": "保持相邻以便拦截"}})\n'
)

DEFAULT_PROMPT_TEMPLATE = (
    DEFAULT_PROMPT_HEADER
    + DEFAULT_PROMPT_RULES
    + DEFAULT_PROMPT_TOOL_GUIDE
    + DEFAULT_PROMPT_EXAMPLE
    + DEFAULT_PROMPT_GUARD_GUIDE
    + DEFAULT_PROMPT_GUARD_EXAMPLE
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
    model_cfg: Mapping[str, Any],
    prompt_template: Optional[str | list[str]] = None,
    allowed_names: Optional[str] = None,
    appearance: Optional[str] = None,
    quotes: Optional[list[str] | str] = None,
    relation_brief: Optional[str] = None,
    weapon_brief: Optional[str] = None,
    tools: Optional[Iterable[object]] = None,
) -> ReActAgent:
    """Create an LLM-backed NPC using Kimi's OpenAI-compatible API."""
    api_key = os.environ["MOONSHOT_API_KEY"]
    base_url = str(model_cfg.get("base_url") or os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1"))
    sec = dict(model_cfg.get("npc") or {})
    model_name = sec.get("model") or os.getenv("KIMI_MODEL", "kimi-k2-turbo-preview")

    tools_text = "perform_attack(), advance_position(), adjust_relation(), transfer_item(), set_protection(), clear_protection()"
    tpl = _join_lines(prompt_template)

    appearance_text = (appearance or "外观描写未提供，可根据设定自行补充细节。").strip()
    if not appearance_text:
        appearance_text = "外观描写未提供，可根据设定自行补充细节。"
    if isinstance(quotes, (list, tuple)):
        quote_items = [str(q).strip() for q in quotes if str(q).strip()]
        quotes_text = " / ".join(quote_items)
    elif isinstance(quotes, str):
        quotes_text = quotes.strip()
    else:
        quotes_text = "保持原角色语气自行发挥。"
    if not quotes_text:
        quotes_text = "保持原角色语气自行发挥。"

    relation_text = (relation_brief or "暂无明确关系记录，默认保持谨慎中立。").strip()
    if not relation_text:
        relation_text = "暂无明确关系记录，默认保持谨慎中立。"

    format_args = {
        "name": name,
        "persona": persona,
        "appearance": appearance_text,
        "quotes": quotes_text,
        "relation_brief": relation_text,
        "weapon_brief": (weapon_brief or "无"),
        "tools": tools_text,
        "allowed_names": allowed_names or "Doctor, Amiya",
    }

    sys_prompt = None
    if tpl:
        try:
            sys_prompt = tpl.format(**format_args)
        except Exception:
            sys_prompt = None
    if not sys_prompt:
        try:
            sys_prompt = DEFAULT_PROMPT_TEMPLATE.format(**format_args)
        except Exception:
            sys_prompt = DEFAULT_PROMPT_TEMPLATE.format(
                name=name,
                persona=persona,
                appearance=appearance_text,
                quotes=quotes_text,
                allowed_names=allowed_names or "Doctor, Amiya",
                tools=tools_text,
                relation_brief=relation_text,
            )

    model = OpenAIChatModel(
        model_name=model_name,
        api_key=api_key,
        stream=bool(sec.get("stream", True)),
        client_args={"base_url": base_url},
        generate_kwargs={"temperature": float(sec.get("temperature", 0.7))},
    )

    toolkit = Toolkit()
    if tools:
        for fn in tools:
            try:
                toolkit.register_tool_function(fn)  # type: ignore[arg-type]
            except Exception:
                continue

    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=model,
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
        toolkit=toolkit,
    )
