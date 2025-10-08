from __future__ import annotations

import os
from typing import Iterable, Optional, Mapping, Any

from agentscope.agent import ReActAgent  # type: ignore
from agentscope.formatter import OpenAIChatFormatter  # type: ignore
from agentscope.memory import InMemoryMemory  # type: ignore
from agentscope.model import OpenAIChatModel  # type: ignore
from agentscope.tool import Toolkit  # type: ignore

# No component imports; the model configuration is passed in from main as a mapping.


DEFAULT_INTENT_SCHEMA = (
    '{\n  "intent": "attack|talk|investigate|move|assist|use_item|skill_check|wait",\n'
    '  "target": "目标名称",\n  "skill": "perception|medicine|...",\n'
    '  "ability": "STR|DEX|CON|INT|WIS|CHA",\n  "proficient": true,\n  "dc_hint": 12,\n'
    '  "damage_expr": "1d4+STR",\n  "time_cost": 1\n}'
)

DEFAULT_PROMPT_HEADER = (
    "你是游戏中的NPC：{name}。\n"
    "人设：{persona}\n"
    "外观特征：{appearance}\n"
    "常用语气/台词：{quotes}\n"
    "当前立场提示（仅你视角）：{relation_brief}\n"
)
DEFAULT_PROMPT_RULES = (
    "对话要求：\n"
    "- 先用简短中文说1-2句对白/想法/微动作，符合人设。\n"
    "- 若需要了解环境或位置，请调用 describe_world()。\n"
    "- 当需要执行行动时，直接调用工具（格式：CALL_TOOL tool_name({{\"key\": \"value\"}}))，不要再输出意图 JSON。\n"
    "- 调用工具后等待系统反馈，再根据结果做简短评论或继续对白。\n"
    "- 行动前对照上方立场提示：≥40 视为亲密同伴（避免攻击、优先支援），≥10 为盟友（若要伤害需先说明理由），≤-10 才视为敌方目标，其余保持谨慎中立。\n"
    "- 若必须违背既定关系行事，请在对白中说明充分理由，否则拒绝执行。\n"
    "- 最近两条消息内若仍拿不准局势，可调用 describe_world(detail=True) 获取其他信息，再结合立场判断。\n"
    "- 参与者名称（仅可用）：{allowed_names}\n"
)

DEFAULT_PROMPT_TOOL_GUIDE = (
    "可用工具：\n"
    "- describe_world(detail: bool = False)：获取当前环境、目标、位置等信息。\n"
    "- perform_attack(attacker, defender, ability='STR', proficient=False, target_ac=None, damage_expr='1d4+STR', advantage='none', auto_move=false)：发动攻击并自动结算伤害；若距离不足可令 auto_move=true 尝试先靠近。\n"
    "- auto_engage(attacker, defender, ability='STR', ...)：先移动到触及范围，再进行一次近战攻击。\n"
    "- perform_skill_check(name, skill, dc, advantage='none')：执行技能检定。\n"
    "- advance_position(name, target:[x,y], steps:int)：朝指定坐标逐步接近。\n"
    "- adjust_relation(a, b, delta, reason='')：在合适情境下调整关系。\n"
    "- transfer_item(target, item, n=1)：移交或分配物资。\n"
)

DEFAULT_PROMPT_EXAMPLE = (
    "输出示例：\n"
    "阿米娅压低声音：‘先确认环境与坐标，再按指示行动。’\n"
    'CALL_TOOL describe_world({{"detail": true}})\n'
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
    model_cfg: Mapping[str, Any],
    prompt_template: Optional[str | list[str]] = None,
    allowed_names: Optional[str] = None,
    appearance: Optional[str] = None,
    quotes: Optional[list[str] | str] = None,
    relation_brief: Optional[str] = None,
    tools: Optional[Iterable[object]] = None,
) -> ReActAgent:
    """Create an LLM-backed NPC using Kimi's OpenAI-compatible API."""
    api_key = os.environ["MOONSHOT_API_KEY"]
    base_url = str(model_cfg.get("base_url") or os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1"))
    sec = dict(model_cfg.get("npc") or {})
    model_name = sec.get("model") or os.getenv("KIMI_MODEL", "kimi-k2-turbo-preview")

    tools_text = "describe_world()"
    intent_schema = DEFAULT_INTENT_SCHEMA
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
        "tools": tools_text,
        "intent_schema": intent_schema,
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
                intent_schema=intent_schema,
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
