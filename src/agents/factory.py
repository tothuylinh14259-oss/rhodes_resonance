from __future__ import annotations

import os
from typing import Iterable, Optional, Mapping, Any

from agentscope.agent import ReActAgent  # type: ignore
from agentscope.formatter import OpenAIChatFormatter  # type: ignore
from agentscope.memory import InMemoryMemory  # type: ignore
from agentscope.model import OpenAIChatModel  # type: ignore
from agentscope.tool import Toolkit  # type: ignore

# No component imports; the model configuration is passed in from main as a mapping.
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
    sys_prompt: Optional[str] = None,
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

    # If sys_prompt is provided by caller, use it directly; otherwise, optionally
    # format the provided prompt_template; if none, fall back to a minimal prompt.
    if sys_prompt is None or not str(sys_prompt).strip():
        sys_prompt_built = None
        if tpl:
            try:
                sys_prompt_built = tpl.format(**format_args)
            except Exception:
                sys_prompt_built = None
        if not sys_prompt_built:
            sys_prompt_built = (
                f"你是游戏中的NPC：{name}. 人设：{persona}. 参与者：{allowed_names or 'Doctor, Amiya'}. 可用工具：{tools_text}"
            )
        sys_prompt = sys_prompt_built

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
