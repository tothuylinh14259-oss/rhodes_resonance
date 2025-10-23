#!/usr/bin/env python3
from __future__ import annotations

"""
Central Orchestrator (main layer)

职责：
- 加载配置、创建日志上下文；
- 构造 world 端口、actions 工具、agents 工厂；
- 通过依赖注入调用 run_demo（已内联自原 runtime.engine）。

注意：Prompt & Context Policy 在本文件顶部集中定义。
凡是“会输入到模型”的上下文（系统提示、环境概要、回合回顾、可及目标预览、私人提示等），
都在下方的 Policy 区块配置。你只需修改那个区块即可全面控制模型看到的内容与顺序。
"""

import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Tuple
from pathlib import Path

"""Top-level optional imports for the Agentscope runtime.

These are optional at import time to allow unit tests that only exercise
logging/types to import this module without having `agentscope` installed.
At runtime (when actually building/running agents), the real library must
be available; otherwise a clear error is raised when used.
"""

try:  # optional at import time (unit tests may not install agentscope)
    from agentscope.agent import AgentBase, ReActAgent  # type: ignore
    from agentscope.message import Msg  # type: ignore
    from agentscope.pipeline import MsgHub  # type: ignore
except Exception:  # pragma: no cover - provide light stubs for tests

    class AgentBase:  # type: ignore
        pass

    class ReActAgent:  # type: ignore
        pass

    class Msg:  # type: ignore
        pass

    class MsgHub:  # type: ignore
        pass


from dataclasses import asdict, is_dataclass, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from itertools import count
from threading import Lock

import logging
import os
import world.tools as world_impl

# ============================================================
# Prompt & Context Policy (EDIT HERE to control model input)
# ============================================================

# Injection toggles and order (what gets added to the model's context memory each turn)
# Order tokens: "env" (环境概要), "recap" (最近播报回顾), "reach_preview" (可及目标/相邻单位预览 + 硬规则行),
#               "private"（本回合私有提示）
CTX_INJECTION_ORDER = ["env", "recap", "reach_preview", "private"]
CTX_INJECT_ENV_SUMMARY = True
CTX_INJECT_RECAP = True
CTX_INJECT_REACH_PREVIEW = True

# How to attach the per-turn private section for the acting NPC
#   - "system": 拼到 system prompt 里（与角色模板同体）
#   - "memory": 作为本回合的 Host 提示注入到 memory（不拼 system）
#   - "off": 不注入
CTX_PRIVATE_SECTION_MODE = "memory"  # "system" | "memory" | "off"

# Whether to broadcast world/recap context to observers (does not directly feed the model,
# but affects what goes into recap on future turns)
CTX_BROADCAST_CONTEXT_TO_OBSERVERS = True

# Debug: dump the final system prompt and per-turn injected memory for each actor
# to logs/prompts/*.txt so you can inspect exactly what was sent to the model.
DEBUG_DUMP_PROMPTS = True

# Recap limits (how many recent broadcasts are summarized)
DEFAULT_RECAP_MSG_LIMIT = 6
DEFAULT_RECAP_ACTION_LIMIT = 6

# System prompt building (tools list + templates)
DEFAULT_TOOLS_TEXT = "perform_attack(), cast_arts(), advance_position(), adjust_relation(), transfer_item(), set_protection(), clear_protection(), first_aid()"

# --- Default system prompt templates ---
DEFAULT_PROMPT_HEADER = (
    "你是：{name}.\n"
    "人设：{persona}\n"
    "外观特征：{appearance}\n"
    "常用语气/台词：{quotes}\n"
    "当前立场提示（仅你视角）：{relation_brief}\n"
)

DEFAULT_PROMPT_RULES = (
    "对话要求：\n"
    "- 先用中文说1-2句对白/想法/微动作，符合人设。\n"
    "- 明确指令优先：若本回合的私有提示中出现“优先处理对白”，你应当首先回应，不能忽略或转移话题。\n"
    '- 当需要执行行动时，直接调用工具（格式：CALL_TOOL tool_name({{"key": "value"}}))。\n'
    "- 作战规则（硬性）：只能对reach_preview里的“可及目标”使用 perform_attack；若目标不在“可及目标”，必须先用 advance_position 进入触及范围后再发动攻击。\n"
    "- 有效行动要求：当存在敌对关系（关系<=-10）时，每回合至少进行一次有效行动。\n"
    "- 行动前对照上方立场提示：≥40 视为亲密同伴（避免攻击、优先支援），≥10 为盟友（若要伤害需先说明理由），≤-10 才视为敌方目标，其余保持谨慎中立。\n"
    "- 若必须违背既定关系行事或违反作战硬规则，请在对白中说明充分理由，并拒绝执行，同时给出更稳妥的替代行动。\n"
    '- 不要输出任何"系统提示"或括号内的系统旁白；只输出对白与 CALL_TOOL。\n'
    "- 参与者名称（仅可用）：{allowed_names}\n"
)

DEFAULT_PROMPT_TOOL_GUIDE = (
    "可用工具（必须提供行动理由）：\n"
    "- perform_attack(attacker, defender, weapon, reason)：使用指定武器发起攻击，仅能对“可及目标”使用。\n"
    "- cast_arts(attacker, art, target, reason)：施放源石技艺，仅能对“可及目标”使用。\n"
    "- advance_position(name, target:[x,y], reason)：朝指定坐标接近（自动使用剩余移动力）；target 必须为 [x,y] 数组。\n"
    "- adjust_relation(a, b, value, reason)：在合适情境下将关系直接设为目标值。\n"
    "- transfer_item(target, item, n=1, reason)：移交或分配物资。\n"
    "- set_protection(guardian, protectee, reason)：建立守护关系（guardian 将在相邻且有反应时替代 protectee 承受攻击）。\n"
    '- clear_protection(guardian="", protectee="", reason)：清除守护关系；可按守护者/被保护者/全部清理。\n'
    "- first_aid(name, target, reason)：对目标进行急救（First Aid）；成功可稳定濒死（HP至少1）或为新伤回复1点HP。\n"
)

DEFAULT_PROMPT_EXAMPLE = (
    "输出示例：\n"
    "阿米娅压低声音：'靠近目标位置。'\n"
    'CALL_TOOL advance_position({{"name": "Amiya", "target": [1, 1], "reason": "接近掩体"}})\n'
)

## Removed guard guide/example blocks per request

DEFAULT_PROMPT_TEMPLATE = (
    DEFAULT_PROMPT_HEADER
    + DEFAULT_PROMPT_RULES
    + DEFAULT_PROMPT_TOOL_GUIDE
    + DEFAULT_PROMPT_EXAMPLE
)

# World summary templates (rendered text; not called "系统提示"避免联想)
WORLD_SUMMARY_HEADER = (
    "现在：地点 {location}；时间 {hh:02d}:{mm:02d}；天气 {weather}"
)
WORLD_SUMMARY_DETAILS = "环境细节：{details}"
WORLD_SUMMARY_OBJECTIVES = "目标：{objectives}"
WORLD_SUMMARY_POSITIONS = "坐标：{positions}"
WORLD_SUMMARY_CHARACTERS = "角色：{chars}"

# Recap
RECAP_TITLE = "回顾"
RECAP_SECTION_RECENT = "刚才...："
RECAP_CLIP_CHARS = 160

# Reach preview & hard rule line
REACH_RULE_LINE = "作战规则：只能对reach_preview的“可及目标”使用 perform_attack。"
REACH_LABEL_ADJ = "相邻（≤1步）{tail}："
REACH_LABEL_TARGETS = "可及武器（{weapon}，触及 {steps}步）可用目标："
REACH_LABEL_ARTS = "可及术式（{art}，触及 {steps}步）可用目标："

# Player/private tips
PLAYER_CTRL_TITLE = "玩家控制提示（仅你可见）：" 
PLAYER_CTRL_LINE = "- 你是玩家操控的角色，请严格执行玩家的意图：{text}"
PRIV_SPEECH_TITLE = "优先处理对白（仅你可见）："
PRIV_SPEECH_SPEAKER = "- 说话者：{who}"
PRIV_SPEECH_CONTENT = "- 内容：{text}"
PRIV_SPEECH_REL = "- 你对该角色的关系：{score:+d}（{label}）"
PRIV_TURN_RES_TITLE = "回合资源（仅你可见）："
PRIV_DIST_TITLE = "距离提示（仅你可见）："
PRIV_DIST_LINE = "- {who}：{dist}步"
PRIV_DIST_UNKNOWN_LINE = "- {who}：未记录"
PRIV_DIST_CANNOT_COMPUTE = "- 无法计算：未记录你的坐标"
PRIV_VALID_ACTION_RULE_LINE = "有效行动要求：存在敌对关系时，每回合必须进行一次有效行动；对超出触及范围的 perform_attack 视为无效。"
PRIV_STATUS_TITLE = "你现在...："
# Lore-style, per-status private prompts (one line per status). Keys are lower-case.
# Use {duration} for "剩余 N 回合"或"持续"，{turns}为整数回合数，{hp}可用于濒死。
PRIV_STATUS_LORE = {
    # 系统类
    "dying": "- 濒死：生命体征极度衰弱（HP={hp}）；若无医疗干员介入，你将在 {turns} 回合后失去生命；再次受创将立即死亡。",
    "dead": "- 死亡：心肺停搏，源石反应趋零（持续）。",
    # 控制类（源石技艺/战术效果）
    "silenced": "- 沉默：术式回路被扰动，无法引导源石技艺（{duration}，禁止施法）。",
    "rooted": "- 定身：地形/拘束装置限制移动（{duration}，禁止移动）。",
    "immobilized": "- 定身：肢体被制动，难以挪步（{duration}，禁止移动）。",
    "restrained": "- 束缚：被网缚/钳制，行动受限（{duration}，禁止移动与攻击）。",
    "stunned": "- 震慑：神经冲击导致短暂恍惚（{duration}，无法行动）。",
    "paralyzed": "- 麻痹：神经传导受阻，肌体失去响应（{duration}，完全无法行动）。",
    "sleep": "- 睡眠：意识离线，处于低反应阶段（{duration}，无法行动）。",
    "frozen": "- 冻结：低温抑制导致全身僵直（{duration}，无法行动）。",
}
PRIV_STATUS_LORE_DEFAULT = "- {state}：效果生效中（{duration}）"

# === End of Policy ===

# ============================================================
# Settings Loader (inline)
# ============================================================


def project_root() -> Path:
    """Return repository root (folder that contains configs/ and src/).

    Walk upwards from this file to find a directory that contains a
    `configs/` folder. Fallback to two levels up from this file.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "configs").exists():
            return parent
    try:
        return (
            here.parents[1]
            if (here.parents[1] / "configs").exists()
            else here.parents[2]
        )
    except Exception:
        return here.parents[1]


def _configs_dir() -> Path:
    return project_root() / "configs"


def _load_json(path: Path) -> dict:
    # no fallback: read and propagate errors if any
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"expected object at {path}, got {type(data).__name__}")
    return data


@dataclass
class ModelConfig:
    base_url: str = "https://api.moonshot.cn/v1"
    npc: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "ModelConfig":
        return ModelConfig(
            base_url=str(d.get("base_url", "https://api.moonshot.cn/v1")),
            npc=dict(d.get("npc") or {}),
        )


def load_model_config() -> ModelConfig:
    return ModelConfig.from_dict(_load_json(_configs_dir() / "model.json"))


def load_story_config(selected_id: Optional[str] = None) -> dict:
    """Load story configuration.

    Supports two shapes:
      1) Single-story (legacy): the file is the story object itself.
      2) Multi-story container: {"active_id": "id", "stories": {"id": {..}, ...}}

    At runtime we always return a single-story object (the active one) so
    the rest of the engine remains unchanged.
    """
    data = _load_json(_configs_dir() / "story.json")
    if not data:
        # no fallback: require explicit story config
        raise FileNotFoundError(
            "configs/story.json is missing or empty; fallback removed"
        )

    try:
        # container shape (ignore any legacy active_id; prefer explicit selection)
        if isinstance(data, dict) and isinstance(data.get("stories"), dict):
            stories = data.get("stories") or {}
            sid = ""
            sel = str(selected_id).strip() if selected_id is not None else ""
            if sel and sel in stories:
                sid = sel
            else:
                # stable default: first by sorted order
                sid = sorted(stories.keys())[0] if stories else ""
            story = stories.get(sid) if sid else None
            if isinstance(story, dict):
                return story
            # container present but empty/malformed -> fallthrough to legacy
    except Exception:
        pass
    # legacy single-story
    return data


def load_characters() -> dict:
    return _load_json(_configs_dir() / "characters.json")


def load_weapons() -> dict:
    return _load_json(_configs_dir() / "weapons.json")


def load_arts() -> dict:
    path = _configs_dir() / "arts.json"
    if not path.exists():
        return {}
    data = _load_json(path)
    if not isinstance(data, dict):
        return {}
    return data


# ============================================================
# Agent Factory (inline)
# ============================================================

try:  # optional at import time (unit tests may not install agentscope)
    from agentscope.formatter import OpenAIChatFormatter  # type: ignore
    from agentscope.memory import InMemoryMemory  # type: ignore
    from agentscope.model import OpenAIChatModel  # type: ignore
    from agentscope.tool import Toolkit  # type: ignore
except Exception:  # pragma: no cover - provide light stubs for tests

    class OpenAIChatFormatter:  # type: ignore
        pass

    class InMemoryMemory:  # type: ignore
        pass

    class OpenAIChatModel:  # type: ignore
        def __init__(self, *a, **k):
            pass

    class Toolkit:  # type: ignore
        def register_tool_function(self, *a, **k):
            pass


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
    prompt_template: Optional[str | List[str]] = None,
    sys_prompt: Optional[str] = None,
    allowed_names: Optional[str] = None,
    appearance: Optional[str] = None,
    quotes: Optional[List[str] | str] = None,
    relation_brief: Optional[str] = None,
    weapon_brief: Optional[str] = None,
    arts_brief: Optional[str] = None,
    tools: Optional[List[object]] = None,
) -> ReActAgent:
    """Create an LLM-backed NPC using an OpenAI-compatible API.

    优先读取环境变量中的 base_url、model、api_key；若未设置，则回退到配置文件与默认值。
    支持的环境变量名（按优先级）：
      - API Key: api_key, OPENAI_API_KEY, API_KEY, MOONSHOT_API_KEY
      - Base URL: base_url, OPENAI_BASE_URL, BASE_URL, KIMI_BASE_URL
      - Model: model, OPENAI_MODEL, MODEL, KIMI_MODEL
    """
    api_key = (
        os.getenv("api_key")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("API_KEY")
        or os.getenv("MOONSHOT_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError(
            "No API key found. Please set api_key or one of OPENAI_API_KEY, API_KEY, or MOONSHOT_API_KEY."
        )
    sec = dict(model_cfg.get("npc") or {})
    base_url = str(
        os.getenv("base_url")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("BASE_URL")
        or os.getenv("KIMI_BASE_URL")
        or model_cfg.get("base_url")
        or "https://api.moonshot.cn/v1"
    )
    model_name = (
        os.getenv("model")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("MODEL")
        or os.getenv("KIMI_MODEL")
        or sec.get("model")
        or "kimi-k2-turbo-preview"
    )

    tools_text = DEFAULT_TOOLS_TEXT
    tpl = _join_lines(prompt_template)

    appearance_text = (
        (appearance or "外观描写未提供，可根据设定自行补充细节。").strip()
        if isinstance(appearance, str)
        else "外观描写未提供，可根据设定自行补充细节。"
    )
    if isinstance(quotes, (list, tuple)):
        quote_items = [str(q).strip() for q in quotes if str(q).strip()]
        quotes_text = " / ".join(quote_items)
    elif isinstance(quotes, str):
        quotes_text = quotes.strip() or "保持原角色语气自行发挥。"
    else:
        quotes_text = "保持原角色语气自行发挥。"
    relation_text = (
        relation_brief or "暂无明确关系记录，默认保持谨慎中立。"
    ).strip() or "暂无明确关系记录，默认保持谨慎中立。"

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

    if sys_prompt is None or not str(sys_prompt).strip():
        # Build using unified system prompt function (consistent with ephemeral agents)
        try:
            sys_prompt = build_sys_prompt(
                name=name,
                persona=persona,
                appearance=appearance,
                quotes=quotes,
                relation_brief=relation_brief,
                weapon_brief=weapon_brief,
                arts_brief=arts_brief,
                allowed_names=(allowed_names or "Doctor, Amiya"),
                prompt_template=(tpl if tpl else None),
                tools_text=tools_text,
            )
        except Exception as e:
            # no fallback: make the failure explicit
            raise

    # Construct model (requires agentscope installed at runtime)
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


# ============================================================
# Actions (inline)
# ============================================================


def make_npc_actions(*, world: Any) -> Tuple[List[object], Dict[str, object]]:
    """Create action tools bound to a provided world API (duck-typed).

    The `world` object is expected to provide functions:
      - attack_with_weapon(...)
      - skill_check_coc(...)
      - move_towards(...)
      - set_relation(...)
      - grant_item(...)
      - set_guard(...)
      - clear_guard(...)
    """

    _ACTION_LOGGER = logging.getLogger("npc_talk_demo")

    def _log_action(msg: str) -> None:
        try:
            if not msg:
                return
            _ACTION_LOGGER.info(f"[ACTION] {msg}")
        except Exception:
            pass

    # Use world-level validated dispatch so all parameter/participants checks are centralized.
    try:
        _VALIDATED = world_impl.validated_tool_dispatch()  # type: ignore[attr-defined]
    except Exception:
        _VALIDATED = {}

    def perform_attack(
        attacker,
        defender,
        weapon: str,
        reason: str = "",
    ):
        fn = _VALIDATED.get("perform_attack") or (
            lambda **p: world.attack_with_weapon(**p)
        )
        resp = fn(attacker=attacker, defender=defender, weapon=weapon)
        meta = resp.metadata or {}
        hit = meta.get("hit")
        dmg = meta.get("damage_total")
        hp_before = meta.get("hp_before")
        hp_after = meta.get("hp_after")
        reason_text = str(reason).strip() or "未提供"
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
        except Exception:
            pass
        try:
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"attack {attacker} -> {defender} using {meta.get('weapon_id')} | hit={hit} dmg={dmg} hp:{hp_before}->{hp_after} "
            f"reach_ok={meta.get('reach_ok')} reason={reason_text}"
        )
        return resp

    def perform_skill_check(
        name, skill, dc=None, advantage: str = "none", reason: str = ""
    ):
        # CoC percentile skill check; dc ignored for compatibility
        resp = world.skill_check_coc(name=name, skill=skill)
        meta = resp.metadata or {}
        success = meta.get("success")
        roll = meta.get("roll")
        target = meta.get("target")
        level = meta.get("success_level")
        reason_text = str(reason).strip() or "未提供"
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"skill_check {name} skill={skill} -> success={success} level={level} roll={roll}/{target} reason={reason_text}"
        )
        return resp

    def advance_position(name, target, reason: str = ""):
        """Move towards target using available movement; no `steps` parameter exposed.

        - Delegates to world.move_towards(name, target) which auto-uses remaining
          movement and accounts for per-turn tokens.
        """
        fn = _VALIDATED.get("advance_position") or (lambda **p: world.move_towards(**p))
        resp = fn(name=name, target=target)
        meta = resp.metadata or {}
        reason_text = str(reason).strip() or "未提供"
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"move {name} -> {target} steps=auto moved={meta.get('moved')} remaining={meta.get('remaining')} reason={reason_text}"
        )
        return resp

    def adjust_relation(a, b, value, reason: str = ""):
        fn = _VALIDATED.get("adjust_relation") or (lambda **p: world.set_relation(**p))
        resp = fn(a=a, b=b, value=value, reason=reason or "")
        meta = resp.metadata or {}
        try:
            meta["call_reason"] = str(reason).strip() or "未提供"
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"relation {a}->{b} set={value} score={meta.get('score')} reason={reason or '无'}"
        )
        return resp

    def transfer_item(target, item, n: int = 1, reason: str = ""):
        fn = _VALIDATED.get("transfer_item") or (lambda **p: world.grant_item(**p))
        resp = fn(target=target, item=item, n=n)
        meta = resp.metadata or {}
        reason_text = str(reason).strip() or "未提供"
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"transfer item={item} -> {target} qty={n} total={meta.get('count')} reason={reason_text}"
        )
        return resp

    def set_protection(guardian: str, protectee: str, reason: str = ""):
        fn = _VALIDATED.get("set_protection") or (lambda **p: world.set_guard(**p))
        resp = fn(guardian=guardian, protectee=protectee)
        meta = resp.metadata or {}
        reason_text = str(reason).strip() or "未提供"
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(f"protect {guardian} -> {protectee} reason={reason_text}")
        return resp

    def clear_protection(guardian: str = "", protectee: str = "", reason: str = ""):
        g = guardian if guardian else None
        p = protectee if protectee else None
        fn = _VALIDATED.get("clear_protection") or (
            lambda **pp: world.clear_guard(**pp)
        )
        resp = fn(guardian=g, protectee=p)
        meta = resp.metadata or {}
        reason_text = str(reason).strip() or "未提供"
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(f"clear_protect guardian={g} protectee={p} reason={reason_text}")
        return resp

    def first_aid(name: str, target: str, reason: str = ""):
        fn = _VALIDATED.get("first_aid") or (lambda **p: world.first_aid(**p))
        resp = fn(name=name, target=target)
        meta = resp.metadata or {}
        reason_text = str(reason).strip() or "未提供"
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"first_aid rescuer={name} target={target} ok={meta.get('ok')} stabilized={meta.get('stabilized')} healed={meta.get('healed')} reason={reason_text}"
        )
        return resp

    def cast_arts(attacker: str, art: str, target: str, reason: str = ""):
        fn = _VALIDATED.get("cast_arts") or (lambda **p: world.cast_arts(**p))
        # 不再接受/透传 mp_spent，由底层按术式规则自动结算
        kwargs = {"attacker": attacker, "art": art, "target": target}
        resp = fn(**kwargs)
        meta = resp.metadata or {}
        reason_text = str(reason).strip() or "未提供"
        try:
            resp.content = list(getattr(resp, "content", []) or [])
            resp.content.append({"type": "text", "text": f"理由：{reason_text}"})
            meta["call_reason"] = reason_text
            resp.metadata = meta
        except Exception:
            pass
        _log_action(
            f"cast_arts {attacker} -> {target} art={art} success={meta.get('success')} reason={reason_text}"
        )
        return resp

    tool_list: List[object] = [
        perform_attack,
        cast_arts,
        advance_position,
        adjust_relation,
        transfer_item,
        set_protection,
        clear_protection,
        first_aid,
    ]
    tool_dispatch: Dict[str, object] = {
        "perform_attack": perform_attack,
        "cast_arts": cast_arts,
        "advance_position": advance_position,
        "adjust_relation": adjust_relation,
        "transfer_item": transfer_item,
        "set_protection": set_protection,
        "clear_protection": clear_protection,
        "first_aid": first_aid,
    }

    return tool_list, tool_dispatch


# ============================================================
# Eventlog (inline)
# ============================================================


class EventType(str, Enum):
    """Supported event categories for the demo logging pipeline."""

    TURN_START = "turn_start"
    TURN_END = "turn_end"
    ACTION = "action"
    STATE_UPDATE = "state_update"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    NARRATIVE = "narrative"
    SYSTEM = "system"


def _clean_value(value: Any) -> Any:
    """Recursively remove ``None`` values from dictionaries/lists."""

    if isinstance(value, dict):
        return {k: _clean_value(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean_value(v) for v in value if v is not None]
    if isinstance(value, tuple):
        cleaned = [_clean_value(v) for v in value if v is not None]
        return cleaned
    return value


@dataclass
class Event:
    """Structured event emitted by the runtime."""

    event_type: EventType
    turn: Optional[int] = None
    phase: Optional[str] = None
    actor: Optional[str] = None
    step: Optional[int] = None
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[datetime] = None
    sequence: Optional[int] = None
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, EventType):
            try:
                self.event_type = EventType(str(self.event_type))
            except Exception as exc:  # pragma: no cover - defensive
                raise ValueError(f"Unsupported event type: {self.event_type}") from exc
        if self.data is None:
            self.data = {}
        if not isinstance(self.data, dict):
            raise TypeError("Event.data must be a dict")
        self.data = _clean_value(self.data)  # drop ``None`` children

    @property
    def event_id(self) -> str:
        seq = self.sequence or 0
        return f"EVT-{seq:06d}"

    def assign_runtime_fields(self, sequence: int, timestamp: datetime) -> None:
        self.sequence = sequence
        self.timestamp = timestamp

    def validate(self) -> None:
        # Validation: most event types require specific fields; state_update allows
        # either a full snapshot (state) or a delta (positions/in_combat/reaction_available)
        if self.event_type is EventType.STATE_UPDATE:
            if "state" in self.data:
                return
            delta_ok = any(
                k in self.data for k in ("positions", "in_combat", "reaction_available")
            )
            if not delta_ok:
                raise ValueError(
                    "Event 'state_update' missing required fields: state (or positions/in_combat/reaction_available)"
                )
            return

        required_keys: Dict[EventType, List[str]] = {
            EventType.ACTION: ["action"],
            EventType.TOOL_CALL: ["tool"],
            EventType.TOOL_RESULT: ["tool"],
            EventType.ERROR: ["message"],
            EventType.NARRATIVE: ["text"],
        }
        expected = required_keys.get(self.event_type)
        if expected:
            missing = [key for key in expected if key not in self.data]
            if missing:
                raise ValueError(
                    f"Event '{self.event_type.value}' missing required fields: {', '.join(missing)}"
                )

    def to_dict(self) -> Dict[str, Any]:
        if self.timestamp is None or self.sequence is None:
            raise RuntimeError(
                "Event must be normalised by EventBus before serialisation"
            )
        payload: Dict[str, Any] = {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
        }
        if self.turn is not None:
            payload["turn"] = self.turn
        if self.phase is not None:
            payload["phase"] = self.phase
        if self.actor is not None:
            payload["actor"] = self.actor
        if self.step is not None:
            payload["step"] = self.step
        if self.correlation_id is not None:
            payload["correlation_id"] = self.correlation_id
        for k, v in self.data.items():
            payload[k] = v
        return payload


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SequenceGenerator:
    def __init__(self) -> None:
        self._counter = count(1)

    def next(self) -> int:
        return next(self._counter)


EventHandler = Callable[[Event], None]


class EventBus:
    """Simple synchronous event bus with monotonic sequence numbers."""

    def __init__(self) -> None:
        self._handlers: List[EventHandler] = []
        self._seq = SequenceGenerator()

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        self._handlers.append(handler)

        def _unsubscribe() -> None:
            try:
                self._handlers.remove(handler)
            except ValueError:
                pass

        return _unsubscribe

    def publish(self, event: Event) -> Event:
        event.assign_runtime_fields(self._seq.next(), utc_now())
        event.validate()
        errors: List[Exception] = []
        for handler in list(self._handlers):
            try:
                handler(event)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
        if errors:
            raise RuntimeError("One or more logging handlers failed") from errors[0]
        return event

    def clear(self) -> None:
        self._handlers.clear()


class StructuredLogger:
    """Write structured events to a JSON Lines file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._file = self._prepare_file(path)

    @staticmethod
    def _prepare_file(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("w", encoding="utf-8")

    def handle(self, event: Event) -> None:
        record = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._lock:
            self._file.write(record + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()

    @property
    def path(self) -> Path:
        return self._path


class StoryLogger:
    """Persist human-readable narrative lines extracted from events.

    This logger is intentionally opinionated: it keeps the core story flow
    (dialogues/narration and action results) and filters out meta prompts
    like per-turn recaps and round banners to avoid perceived duplicates
    in the human-readable story log. Structured logs remain full-fidelity.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._file = self._prepare_file(path)
        # Keep only the first world-summary (opening background); subsequent
        # summaries are repetitive for human readers.
        self._printed_initial_world_summary = False

    @staticmethod
    def _prepare_file(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("w", encoding="utf-8")

    def handle(self, event: Event) -> None:
        # Only record human-facing narrative lines
        if event.event_type is not EventType.NARRATIVE:
            return

        # Filter out meta narrative that causes duplication/noise in story log
        phase = (event.phase or "").strip()
        if phase.startswith("context:"):
            # e.g. pre-turn recap blocks
            return
        if phase == "round-start":
            # e.g. "第N回合：小队行动" banners
            return
        if phase == "world-summary":
            # keep only the first world summary as the opening background
            if self._printed_initial_world_summary:
                return
            self._printed_initial_world_summary = True

        text = event.data.get("text", "")
        actor = event.actor or "system"
        timestamp = event.timestamp.isoformat() if event.timestamp else ""
        line = f"[{event.event_id}] {timestamp} {actor}: {text}"
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()

    @property
    def path(self) -> Path:
        return self._path


@dataclass
class LoggingContext:
    bus: EventBus
    structured: StructuredLogger
    story: StoryLogger

    def close(self) -> None:
        self.structured.close()
        self.story.close()


def create_logging_context(base_path: Optional[Path] = None) -> LoggingContext:
    # Avoid component dependency: `base_path` should be provided by main.
    # Fallback to repository root heuristic (two levels up from this file).
    root = base_path or Path(__file__).resolve().parents[1]
    logs_dir = root / "logs"
    events_path = logs_dir / "run_events.jsonl"
    story_path = logs_dir / "run_story.log"

    bus = EventBus()
    structured = StructuredLogger(events_path)
    story = StoryLogger(story_path)

    bus.subscribe(structured.handle)
    bus.subscribe(story.handle)

    return LoggingContext(bus=bus, structured=structured, story=story)


# ============================================================
# End of Eventlog (inline)
# ============================================================


class _WorldPort:
    """Light adapter around world.tools to avoid component coupling in engine."""

    # bind frequently used world functions as simple static methods
    set_position = staticmethod(world_impl.set_position)
    set_scene = staticmethod(world_impl.set_scene)
    set_relation = staticmethod(world_impl.set_relation)
    get_turn = staticmethod(world_impl.get_turn)
    reset_actor_turn = staticmethod(world_impl.reset_actor_turn)
    end_combat = staticmethod(world_impl.end_combat)
    # CoC 7e support (DnD compatibility removed)
    set_coc_character = staticmethod(world_impl.set_coc_character)
    set_coc_character_from_config = staticmethod(
        world_impl.set_coc_character_from_config
    )
    recompute_coc_derived = staticmethod(world_impl.recompute_coc_derived)
    skill_check_coc = staticmethod(world_impl.skill_check_coc)
    set_weapon_defs = staticmethod(world_impl.set_weapon_defs)
    set_arts_defs = staticmethod(world_impl.set_arts_defs)
    get_arts_defs = staticmethod(world_impl.get_arts_defs)
    attack_with_weapon = staticmethod(world_impl.attack_with_weapon)
    cast_arts = staticmethod(world_impl.cast_arts)
    first_aid = staticmethod(world_impl.first_aid)
    # movement helpers
    get_move_speed_steps = staticmethod(world_impl.get_move_speed_steps)
    derive_move_speed_steps = staticmethod(world_impl.derive_move_speed_steps)
    derive_all_speeds_from_stats = staticmethod(world_impl.derive_all_speeds_from_stats)
    # dying helpers
    tick_dying_for = staticmethod(world_impl.tick_dying_for)
    # tools that actions need directly
    move_towards = staticmethod(world_impl.move_towards)
    # DnD functions removed; CoC only
    grant_item = staticmethod(world_impl.grant_item)
    set_guard = staticmethod(world_impl.set_guard)
    clear_guard = staticmethod(world_impl.clear_guard)
    # unified statuses
    list_statuses = staticmethod(world_impl.list_statuses)
    get_action_restrictions = staticmethod(world_impl.get_action_restrictions)
    # participants and character meta helpers
    set_participants = staticmethod(world_impl.set_participants)
    set_character_meta = staticmethod(world_impl.set_character_meta)

    @staticmethod
    def snapshot() -> Dict[str, Any]:
        return world_impl.WORLD.snapshot()

    @staticmethod
    def runtime() -> Dict[str, Any]:
        W = world_impl.WORLD
        return {
            "version": int(getattr(W, "version", 0)),
            "positions": dict(W.positions),
            "in_combat": bool(W.in_combat),
            "turn_state": dict(W.turn_state),
            "round": int(W.round),
            "characters": dict(W.characters),
            "participants": list(getattr(W, "participants", []) or []),
        }


# Note: DnD compatibility and conversion paths removed; CoC-only runtime.


# ============================================================
# Module-level Constants
# ============================================================

# Relation thresholds for categorization
RELATION_INTIMATE_FRIEND = 60
RELATION_CLOSE_ALLY = 40
RELATION_ALLY = 10
RELATION_HOSTILE = -10
RELATION_ENEMY = -40
RELATION_ARCH_ENEMY = -60

##### Prompt templates moved to top in "Prompt & Context Policy" section #####


def build_sys_prompt(
    *,
    name: str,
    persona: str,
    appearance: str | None,
    quotes: list[str] | str | None,
    relation_brief: str | None,
    weapon_brief: str | None,
    arts_brief: str | None = None,
    allowed_names: str,
    prompt_template: str | list[str] | None = None,
    tools_text: str | None = None,
) -> str:
    """Build system prompt for an NPC using either a custom template or the default.

    Keeps text normalization consistent across call sites.
    """
    # Normalize fields
    appearance_text = (
        appearance or "外观描写未提供，可根据设定自行补充细节。"
    ).strip() or "外观描写未提供，可根据设定自行补充细节。"
    if isinstance(quotes, (list, tuple)):
        items = [str(q).strip() for q in quotes if str(q).strip()]
        quotes_text = " / ".join(items) if items else "保持原角色语气自行发挥。"
    elif isinstance(quotes, str):
        quotes_text = quotes.strip() or "保持原角色语气自行发挥。"
    else:
        quotes_text = "保持原角色语气自行发挥。"
    relation_text = (
        relation_brief or "暂无明确关系记录，默认保持谨慎中立。"
    ).strip() or "暂无明确关系记录，默认保持谨慎中立。"
    tools_txt = tools_text or DEFAULT_TOOLS_TEXT

    args = {
        "name": name,
        "persona": persona,
        "appearance": appearance_text,
        "quotes": quotes_text,
        "relation_brief": relation_text,
        "weapon_brief": (weapon_brief or "无"),
        "arts_brief": (arts_brief or "无"),
        "tools": tools_txt,
        "allowed_names": allowed_names or "Doctor, Amiya",
    }

    # Choose template: provided one (list or str) or the DEFAULT_PROMPT_TEMPLATE
    tpl = None
    if prompt_template is not None:
        try:
            if isinstance(prompt_template, list):
                tpl = "\\n".join(str(x) for x in prompt_template)
            else:
                tpl = str(prompt_template)
        except Exception:
            tpl = None
    if tpl is None:
        tpl = DEFAULT_PROMPT_TEMPLATE

    try:
        return str(tpl.format(**args))
    except Exception as e:
        # no fallback: propagate
        raise


# Tool call pattern
TOOL_CALL_PATTERN = re.compile(r"CALL_TOOL\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")


# ============================================================
# Utility Functions
# ============================================================


def _relation_category(score: int) -> str:
    """Categorize relation score into human-readable labels."""
    if score >= RELATION_INTIMATE_FRIEND:
        return "挚友"
    if score >= RELATION_CLOSE_ALLY:
        return "亲密同伴"
    if score >= RELATION_ALLY:
        return "盟友"
    if score <= RELATION_ARCH_ENEMY:
        return "死敌"
    if score <= RELATION_ENEMY:
        return "仇视"
    if score <= RELATION_HOSTILE:
        return "敌对"
    return "中立"


def _safe_text(msg: Msg) -> str:
    """Extract text content from a Msg object, handling various content formats."""
    try:
        text = msg.get_text_content()
    except Exception:
        text = None
    if text is not None:
        return str(text)
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        lines = []
        for blk in content:
            if hasattr(blk, "text"):
                lines.append(str(getattr(blk, "text", "")))
            elif isinstance(blk, dict):
                lines.append(str(blk.get("text", "")))
        return "\n".join(line for line in lines if line)
    return str(content)


def _clip(text: str, limit: int = 160) -> str:
    """Truncate text to a maximum length with ellipsis."""
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def _extract_json_after(s: str, start_pos: int) -> Tuple[Optional[str], int]:
    """Extract the first balanced JSON object from string starting at position.

    Returns (json_string, end_position) or (None, start_pos) if not found.
    """
    n = len(s)
    i = s.find("{", start_pos)
    if i == -1:
        return None, start_pos
    brace = 0
    in_str = False
    esc = False
    j = i
    while j < n:
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                brace += 1
            elif ch == "}":
                brace -= 1
                if brace == 0:
                    return s[i : j + 1], j + 1
        j += 1
    return None, start_pos


def _parse_tool_calls(text: str) -> List[Tuple[str, dict]]:
    """Parse CALL_TOOL invocations from agent output.

    Supports both formats:
    - CALL_TOOL name({json})
    - CALL_TOOL name\\n{json}
    """
    calls: List[Tuple[str, dict]] = []
    if not text:
        return calls

    idx = 0
    while True:
        m = TOOL_CALL_PATTERN.search(text, idx)
        if not m:
            break
        name = m.group("name")
        scan_from = m.end()
        json_body, end_pos = _extract_json_after(text, scan_from)
        params: dict = {}
        if json_body:
            try:
                params = json.loads(json_body)
            except Exception:
                params = {}
            calls.append((name, params))
            idx = end_pos
        else:
            idx = scan_from
    return calls


def _strip_tool_calls_from_text(text: str) -> str:
    """Return text with all CALL_TOOL ... {json} segments removed."""
    if not text:
        return text

    idx = 0
    out_parts: List[str] = []
    while True:
        m = TOOL_CALL_PATTERN.search(text, idx)
        if not m:
            out_parts.append(text[idx:])
            break
        out_parts.append(text[idx : m.start()])
        scan_from = m.end()
        json_body, end_pos = _extract_json_after(text, scan_from)
        if json_body:
            idx = end_pos
        else:
            idx = scan_from
    return "".join(out_parts)


def _parse_story_positions(raw: Any, target: Dict[str, Tuple[int, int]]) -> None:
    """Extract actor positions from story config and store in target dict."""
    if not isinstance(raw, dict):
        return
    for actor_name, pos in raw.items():
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            try:
                target[str(actor_name)] = (int(pos[0]), int(pos[1]))
            except Exception:
                continue


@dataclass
class TurnContext:
    world: Any
    emit: Callable[..., None]
    tool_dispatch: Dict[str, object]
    tool_list: List[object]
    chat_log: List[Dict[str, Any]]
    action_log: List[Dict[str, Any]]
    last_seen: Dict[str, int]
    current_round: int
    recap_enabled: bool
    recap_msg_limit: int
    recap_action_limit: int
    allowed_set: set[str]
    allowed_names_str: str
    model_cfg: Mapping[str, Any]
    build_agent: Callable[..., ReActAgent]
    debug_dump_prompts: bool = False


def relation_brief_for(world: Any, name: str) -> str:
    try:
        rel_map = dict(world.snapshot().get("relations") or {})
    except Exception:
        rel_map = {}
    if not rel_map:
        return ""
    me = str(name)
    entries: List[str] = []
    for key, raw in rel_map.items():
        try:
            a, b = key.split("->", 1)
        except Exception:
            continue
        if a != me or b == me:
            continue
        try:
            score = int(raw)
        except Exception:
            continue
        label = _relation_category(score)
        entries.append(f"{b}:{score:+d}（{label}）")
    return "；".join(entries)


def weapon_brief_for(world: Any, nm: str) -> str:
    try:
        snap = world.snapshot()
        wdefs = dict((snap.get("weapon_defs") or {}))
        bag = dict((snap.get("inventory") or {}).get(str(nm), {}) or {})
    except Exception:
        return "无"
    entries: List[str] = []
    for wid, count in bag.items():
        if int(count) <= 0 or wid not in wdefs:
            continue
        try:
            reach = int((wdefs[wid] or {}).get("reach_steps", 1))
        except Exception:
            reach = 1
        try:
            dmg = str((wdefs[wid] or {}).get("damage") or "")
        except Exception:
            dmg = ""
        if dmg:
            entries.append(f"{wid}(触及{reach}步, {dmg})")
        else:
            entries.append(f"{wid}(触及{reach}步)")
    return "；".join(entries) if entries else "无"


def arts_brief_for(world: Any, nm: str) -> str:
    """Return a compact brief of arts known by nm with range and MP info."""
    try:
        snap = world.snapshot()
        ch = dict((snap.get("characters") or {}).get(str(nm), {}) or {})
        coc = dict(ch.get("coc") or {})
        known = list(coc.get("arts_known") or [])
        cur_mp = ch.get("mp")
        max_mp = ch.get("max_mp")
    except Exception:
        known = []
        cur_mp = None
        max_mp = None
    try:
        arts_defs = world.get_arts_defs() if hasattr(world, "get_arts_defs") else {}
    except Exception:
        arts_defs = {}
    parts: List[str] = []
    for aid in known:
        a = (arts_defs or {}).get(str(aid)) or {}
        # Display stable internal id instead of human label to avoid confusing renames
        label = str(aid)
        steps = a.get("range_steps", 6)
        mp = a.get("mp") or {}
        cost = (mp or {}).get("cost", 0)
        # 不在提示中呈现“可变/固定”的 MP 模式，避免误导角色去输入 mp_spent
        cskill = a.get("cast_skill") or ""
        cpart = f", 技能{cskill}" if cskill else ""
        parts.append(f"{label}(触及{steps}步, 消耗{cost}{cpart})")
    brief = "；".join(parts) if parts else "无"
    if cur_mp is not None and max_mp is not None:
        return f"MP {cur_mp}/{max_mp}；" + brief
    return brief


def apply_story_position(
    world: Any, story_positions: Dict[str, Tuple[int, int]], name: str
) -> None:
    pos = story_positions.get(str(name))
    if not pos:
        return
    try:
        world.set_position(name, int(pos[0]), int(pos[1]))
    except Exception:
        pass


def normalize_scene_cfg(sc: Optional[Mapping[str, Any]]):
    name = None
    objectives: List[str] = []
    details: List[str] = []
    weather: Optional[str] = None
    time_min: Optional[int] = None
    if isinstance(sc, dict):
        name_candidate = sc.get("name")
        if isinstance(name_candidate, str) and name_candidate.strip():
            name = name_candidate.strip()
        objs = sc.get("objectives")
        if isinstance(objs, list):
            for obj in objs:
                if isinstance(obj, str) and obj.strip():
                    objectives.append(obj.strip())
        details_val = sc.get("details")
        if isinstance(details_val, str) and details_val.strip():
            details = [details_val.strip()]
        elif isinstance(details_val, list):
            for d in details_val:
                if isinstance(d, str) and d.strip():
                    details.append(d.strip())
        tstr = sc.get("time")
        if isinstance(tstr, str) and tstr:
            m = re.match(r"^(\d{1,2}):(\d{2})$", tstr.strip())
            if m:
                hh, mm = int(m.group(1)), int(m.group(2))
                if 0 <= hh < 24 and 0 <= mm < 60:
                    time_min = hh * 60 + mm
        if time_min is None:
            tm = sc.get("time_min", None)
            if isinstance(tm, (int, float)):
                try:
                    time_min = int(tm)
                except Exception:
                    time_min = None
        w = sc.get("weather")
        if isinstance(w, str) and w.strip():
            weather = w.strip()
    return name, objectives, details, weather, time_min


def apply_scene_to_world(world: Any, name, objectives, details, weather, time_min):
    try:
        snap0 = world.snapshot()
        current_loc = str((snap0 or {}).get("location") or "")
    except Exception:
        current_loc = ""
    world.set_scene(
        name or current_loc,
        objectives or None,
        append=False,
        details=details or None,
        time_min=time_min,
        weather=weather,
    )


async def bcast(
    ctx: TurnContext, hub: MsgHub, msg: Msg, *, phase: Optional[str] = None
):
    await hub.broadcast(msg)
    text = _safe_text(msg)
    ctx.emit(
        "narrative",
        actor=msg.name,
        phase=phase,
        data={"text": text, "role": getattr(msg, "role", None)},
    )
    try:
        ctx.chat_log.append(
            {
                "actor": getattr(msg, "name", None),
                "role": getattr(msg, "role", None),
                "text": text,
                "turn": ctx.current_round,
                "phase": phase or "",
            }
        )
    except Exception:
        pass


def emit_turn_state(ctx: TurnContext) -> None:
    try:
        rt = ctx.world.runtime()
        positions = rt.get("positions", {})
        in_combat = bool(rt.get("in_combat"))
        r_avail = rt.get("turn_state", {})
        ctx.emit(
            "state_update",
            phase="turn-state",
            data={
                "positions": {k: list(v) for k, v in positions.items()},
                "in_combat": in_combat,
                "reaction_available": r_avail,
            },
        )
    except Exception as exc:
        ctx.emit(
            "error",
            phase="turn-state",
            data={
                "message": f"获取回合信息失败: {exc}",
                "error_type": "turn_snapshot",
            },
        )


def emit_world_state(ctx: TurnContext, turn_val: int) -> None:
    snapshot = ctx.world.snapshot()
    ctx.emit("state_update", phase="world", turn=turn_val, data={"state": snapshot})


# Removed: legacy dev-only context card writer. Prompt logs supersede it.


def reach_preview_lines(world: Any, name: str) -> List[str]:
    lines: List[str] = []
    try:

        def _fmt_steps(n: int) -> str:
            try:
                s = int(n)
            except Exception:
                s = 0
            if s < 0:
                s = 0
            return f"{s}步"

        snap = world.snapshot() or {}
        pos_map = snap.get("positions") or {}
        if not isinstance(pos_map, dict) or str(name) not in pos_map:
            return lines
        me_pos = pos_map[str(name)]
        if not isinstance(me_pos, (list, tuple)) or len(me_pos) < 2:
            return lines
        me_xy = (int(me_pos[0]), int(me_pos[1]))
        scene_units: List[Tuple[str, Tuple[int, int]]] = []
        for nm, p in (pos_map or {}).items():
            try:
                if not isinstance(p, (list, tuple)) or len(p) < 2:
                    continue
                scene_units.append((str(nm), (int(p[0]), int(p[1]))))
            except Exception:
                continue

        def manhattan(a, b):
            return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))

        try:
            adj = []
            for nm, p in scene_units:
                if nm == str(name):
                    continue
                d = manhattan(me_xy, p)
                if d <= 1:
                    adj.append((nm, int(d)))
            if adj:
                adj.sort(key=lambda t: (t[1], t[0]))
                try:
                    ts = (world.runtime().get("turn_state") or {}).get(
                        str(name), {}
                    ) or {}
                    react_avail = bool(ts.get("reaction_available", True))
                except Exception:
                    react_avail = True
                tail = "（反应：可用）" if react_avail else "（反应：已用）"
                parts = [f"{nm}({_fmt_steps(d)})" for nm, d in adj]
                lines.append(REACH_LABEL_ADJ.format(tail=tail) + ", ".join(parts))
        except Exception:
            pass
        inv = (snap.get("inventory") or {}).get(str(name), {}) or {}
        wdefs = (snap.get("weapon_defs") or {}) or {}
        weapons = []
        for wid, cnt in inv.items():
            try:
                if int(cnt) <= 0:
                    continue
            except Exception:
                continue
            wid_str = str(wid)
            if wid_str not in wdefs:
                continue
            try:
                rsteps = int((wdefs[wid_str] or {}).get("reach_steps", 1))
            except Exception:
                rsteps = 1
            rsteps = max(1, rsteps)
            weapons.append((wid_str, rsteps))
        weapons.sort(key=lambda t: (t[1], t[0]))
        for wid, rsteps in weapons:
            items = []
            for nm, p in scene_units:
                try:
                    d = manhattan(me_xy, p)
                except Exception:
                    continue
                if nm == str(name):
                    continue
                if d <= int(rsteps):
                    items.append((nm, int(d)))
            if not items:
                continue
            items.sort(key=lambda t: (t[1], t[0]))
            parts = [f"{nm}({_fmt_steps(d)})" for nm, d in items]
            lines.append(
                REACH_LABEL_TARGETS.format(weapon=wid, steps=int(rsteps))
                + ", ".join(parts)
            )
        # Arts preview (known arts within range)
        try:
            ch = dict((snap.get("characters") or {}).get(str(name), {}) or {})
            known = list((ch.get("coc") or {}).get("arts_known") or [])
            arts_defs = world.get_arts_defs() if hasattr(world, "get_arts_defs") else {}
            for aid in known:
                a = (arts_defs or {}).get(str(aid)) or {}
                rsteps = int(a.get("range_steps", 6) or 6)
                items = []
                for nm, p in scene_units:
                    if nm == str(name):
                        continue
                    try:
                        d = manhattan(me_xy, p)
                    except Exception:
                        continue
                    if d <= rsteps:
                        items.append((nm, int(d)))
                if not items:
                    continue
                items.sort(key=lambda t: (t[1], t[0]))
                parts = [f"{nm}({_fmt_steps(d)})" for nm, d in items]
                # Use internal id for consistency with action/tool calls
                art_name = str(aid)
                lines.append(
                    REACH_LABEL_ARTS.format(art=art_name, steps=rsteps)
                    + ", ".join(parts)
                )
        except Exception:
            pass
    except Exception:
        return []
    return lines


def make_ephemeral_agent(
    ctx: TurnContext, name: str, private_section: Optional[str]
) -> ReActAgent:
    try:
        sheet_now = (ctx.world.snapshot().get("characters") or {}).get(name, {}) or {}
        persona_now = sheet_now.get("persona") or ""
        appearance_now = sheet_now.get("appearance")
        quotes_now = sheet_now.get("quotes")
    except Exception:
        persona_now = ""
        appearance_now = None
        quotes_now = None

    # Build system prompt (outside the try/except so it always runs)
    sys_prompt_text = build_sys_prompt(
        name=name,
        persona=str(persona_now or ""),
        appearance=appearance_now,
        quotes=quotes_now,
        relation_brief=relation_brief_for(ctx.world, name),
        weapon_brief=weapon_brief_for(ctx.world, name),
        arts_brief=arts_brief_for(ctx.world, name),
        allowed_names=ctx.allowed_names_str,
    )
    if CTX_PRIVATE_SECTION_MODE == "system" and private_section:
        sys_prompt_text = sys_prompt_text + "\n" + private_section
    agent = ctx.build_agent(
        name,
        str(persona_now or ""),
        ctx.model_cfg,
        sys_prompt=sys_prompt_text,
        allowed_names=ctx.allowed_names_str,
        appearance=appearance_now,
        quotes=quotes_now,
        relation_brief=relation_brief_for(ctx.world, name),
        weapon_brief=weapon_brief_for(ctx.world, name),
        arts_brief=arts_brief_for(ctx.world, name),
        tools=ctx.tool_list,
    )
    try:
        setattr(agent, "_debug_sys_prompt", sys_prompt_text)
    except Exception:
        pass
    return agent


async def handle_tool_calls(ctx: TurnContext, origin: Msg, hub: MsgHub):
    text = _safe_text(origin)
    tool_calls = _parse_tool_calls(text)
    if not tool_calls:
        return
    for tool_name, params in tool_calls:
        phase = f"tool:{tool_name}"
        # Soft-accept a pseudo tool `generate_response` without exposing it in toolkit/prompt.
        # We log it but by default do NOT broadcast its text to avoid duplicating the narrative
        # already printed from the cleaned message. This keeps the tool usable without suggesting it.
        if str(tool_name) == "generate_response":
            # Extract optional text and reason
            try:
                t = ""
                if isinstance(params, dict):
                    t = str(
                        params.get("response")
                        or params.get("text")
                        or params.get("message")
                        or ""
                    )
            except Exception:
                t = ""
            try:
                reason_text = str((params or {}).get("reason", "")).strip() or "未提供"
            except Exception:
                reason_text = "未提供"

            # Emit a slim tool_call record (avoid logging full text body)
            ctx.emit(
                "tool_call",
                actor=origin.name,
                phase=phase,
                data={"tool": tool_name, "params": {"text_len": len(t) if t else 0}},
            )
            try:
                ctx.action_log.append(
                    {
                        "actor": origin.name,
                        "tool": tool_name,
                        "type": "call",
                        "params": {"text_len": len(t) if t else 0, "reason": reason_text},
                        "turn": ctx.current_round,
                    }
                )
            except Exception:
                pass

            meta = {"ok": True, "call_reason": reason_text, "text_len": len(t) if t else 0}
            ctx.emit(
                "tool_result",
                actor=origin.name,
                phase=phase,
                data={"tool": tool_name, "metadata": meta, "text": []},
            )
            try:
                ctx.action_log.append(
                    {
                        "actor": origin.name,
                        "tool": tool_name,
                        "type": "result",
                        "text": [],
                        "meta": meta,
                        "turn": ctx.current_round,
                    }
                )
            except Exception:
                pass
            # Do not broadcast content from this pseudo tool by default.
            continue
        func = ctx.tool_dispatch.get(tool_name)
        if not func:
            ctx.emit(
                "error",
                actor=origin.name,
                phase=phase,
                data={
                    "message": f"未知工具调用 {tool_name}",
                    "tool": tool_name,
                    "params": params,
                    "error_type": "tool_not_found",
                },
            )
            continue
        # 参数与参与者校验已下沉至 world 的 validated_tool_dispatch；这里不再做 name/participants 检查。
        try:
            if not str(params.get("reason", "")).strip():
                params["reason"] = "未提供"
        except Exception:
            params["reason"] = "未提供"
        params_slim = dict(params or {})
        if "reason" in params_slim:
            try:
                del params_slim["reason"]
            except Exception:
                pass
        ctx.emit(
            "tool_call",
            actor=origin.name,
            phase=phase,
            data={"tool": tool_name, "params": params_slim},
        )
        try:
            ctx.action_log.append(
                {
                    "actor": origin.name,
                    "tool": tool_name,
                    "type": "call",
                    "params": dict(params or {}),
                    "turn": ctx.current_round,
                }
            )
        except Exception:
            pass
        try:
            resp = func(**params)
        except TypeError as exc:
            ctx.emit(
                "error",
                actor=origin.name,
                phase=phase,
                data={
                    "message": str(exc),
                    "tool": tool_name,
                    "params": params,
                    "error_type": "invalid_parameters",
                },
            )
            continue
        except Exception as exc:
            ctx.emit(
                "error",
                actor=origin.name,
                phase=phase,
                data={
                    "message": str(exc),
                    "tool": tool_name,
                    "params": params,
                    "error_type": exc.__class__.__name__,
                },
            )
            continue
        text_blocks = getattr(resp, "content", None)
        lines: List[str] = []
        if isinstance(text_blocks, list):
            for blk in text_blocks:
                if hasattr(blk, "text"):
                    lines.append(str(getattr(blk, "text", "")))
                elif isinstance(blk, dict):
                    lines.append(str(blk.get("text", "")))
                else:
                    lines.append(str(blk))
        meta = getattr(resp, "metadata", None)
        try:

            def _strip_reason(t: str) -> str:
                s = str(t or "")
                s = re.sub(
                    r"\s*(?:行动)?(?:理由|reason|Reason)[:：][\s\S]*$", "", s
                ).strip()
                if re.match(r"^(?:行动)?(?:理由|reason|Reason)[:：]", s):
                    return ""
                return s

            lines = [x for x in (_strip_reason(x) for x in lines) if x]
        except Exception:
            pass
        ctx.emit(
            "tool_result",
            actor=origin.name,
            phase=phase,
            data={"tool": tool_name, "metadata": meta, "text": lines},
        )
        try:
            ctx.action_log.append(
                {
                    "actor": origin.name,
                    "tool": tool_name,
                    "type": "result",
                    "text": list(lines),
                    "meta": meta,
                    "turn": ctx.current_round,
                }
            )
        except Exception:
            pass
        if not lines:
            continue
        tool_msg = Msg(
            name=f"{origin.name}[tool]",
            content="\n".join(line for line in lines if line),
            role="assistant",
        )
        await bcast(ctx, hub, tool_msg, phase=phase)


def recap_for(ctx: TurnContext, name: str) -> Optional[Msg]:
    if not ctx.recap_enabled:
        return None
    start = int(ctx.last_seen.get(name, 0))
    recent_msgs = [
        e for e in ctx.chat_log[start:] if e.get("actor") not in (None, "Host")
    ]
    if ctx.recap_msg_limit > 0:
        recent_msgs = recent_msgs[-ctx.recap_msg_limit :]
    if not recent_msgs:
        return None
    lines: List[str] = [RECAP_TITLE.format(name=name)]
    lines.append(RECAP_SECTION_RECENT)
    for e in recent_msgs:
        txt = _clip(str(e.get("text") or "").strip(), RECAP_CLIP_CHARS)
        lines.append(f"- {e.get('actor')}: {txt}")
    ctx.last_seen[name] = len(ctx.chat_log)
    return Msg("Host", "\n".join(lines), "assistant")


async def npc_ephemeral_say(
    ctx: TurnContext,
    name: str,
    private_section: Optional[str],
    hub: MsgHub,
    recap_msg: Optional[Msg] = None,
) -> None:
    ephemeral = make_ephemeral_agent(ctx, name, private_section)
    debug_items: List[Tuple[str, str]] = []
    for token in list(CTX_INJECTION_ORDER or []):
        try:
            tk = str(token).strip().lower()
            if tk == "env" and CTX_INJECT_ENV_SUMMARY:
                try:
                    env_text = _world_summary_text(ctx.world.snapshot())
                    await ephemeral.memory.add(Msg("Host", env_text, "assistant"))
                    debug_items.append(("env", env_text))
                except Exception:
                    pass
            elif tk == "recap" and CTX_INJECT_RECAP:
                try:
                    if recap_msg is not None:
                        await ephemeral.memory.add(recap_msg)
                        debug_items.append(("recap", _safe_text(recap_msg)))
                except Exception:
                    pass
            elif tk == "reach_preview" and CTX_INJECT_REACH_PREVIEW:
                try:
                    lines = reach_preview_lines(ctx.world, name)
                    if lines:
                        lines = [REACH_RULE_LINE] + lines
                        text = "\n".join(lines)
                        await ephemeral.memory.add(Msg("Host", text, "assistant"))
                        debug_items.append(("reach_preview", text))
                except Exception:
                    pass
            elif (
                tk == "private"
                and CTX_PRIVATE_SECTION_MODE == "memory"
                and private_section
            ):
                try:
                    await ephemeral.memory.add(
                        Msg("Host", private_section, "assistant")
                    )
                    debug_items.append(("private", private_section))
                except Exception:
                    pass
        except Exception:
            pass
    if ctx.debug_dump_prompts:
        try:
            root = project_root()
            dump_dir = root / "logs" / "prompts"
            dump_dir.mkdir(parents=True, exist_ok=True)
            safe = "".join(
                ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in str(name)
            )
            # Keep only the latest dump per actor; remove older files for this actor
            try:
                for _p in dump_dir.glob(f"{safe}_*.txt"):
                    try:
                        _p.unlink()
                    except Exception:
                        pass
            except Exception:
                pass
            path = dump_dir / f"{safe}_prompt.txt"
            sys_text = str(getattr(ephemeral, "_debug_sys_prompt", ""))
            lines: List[str] = []
            lines.append("=== SYSTEM PROMPT ===")
            lines.append(sys_text)
            lines.append("")
            lines.append("=== MEMORY MESSAGES (in order) ===")
            for tk, txt in debug_items:
                lines.append(f"--- [{tk}] ---")
                lines.append(str(txt or ""))
                lines.append("")
            with path.open("w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass
    out = await ephemeral(None)
    try:
        raw_text = _safe_text(out)
        cleaned = _strip_tool_calls_from_text(raw_text)
        if cleaned and cleaned.strip():
            msg_clean = Msg(
                getattr(out, "name", name),
                cleaned,
                getattr(out, "role", "assistant") or "assistant",
            )
            await bcast(ctx, hub, msg_clean, phase=f"npc:{name}")
        else:
            await bcast(ctx, hub, out, phase=f"npc:{name}")
    except Exception:
        await bcast(ctx, hub, out, phase=f"npc:{name}")
    await handle_tool_calls(ctx, out, hub)


async def run_demo(
    *,
    emit: Callable[..., None],
    build_agent: Callable[..., ReActAgent],
    tool_fns: List[object] | None,
    tool_dispatch: Dict[str, object] | None,
    # prompts removed: prompt assembly moved to main; templates now come from defaults
    model_cfg: Mapping[str, Any],
    story_cfg: Mapping[str, Any],
    characters: Mapping[str, Any],
    world: Any,
    player_input_provider: Optional[Callable[[str], Awaitable[str]]] = None,
    pause_gate: Optional[object] = None,
) -> None:
    """Run the NPC talk demo (sequential group chat, no GM/adjudication)."""

    story_positions: Dict[str, Tuple[int, int]] = {}

    if isinstance(story_cfg, dict):
        _parse_story_positions(
            story_cfg.get("initial_positions") or {}, story_positions
        )
        _parse_story_positions(story_cfg.get("positions") or {}, story_positions)
        initial_section = story_cfg.get("initial")
        if isinstance(initial_section, dict):
            _parse_story_positions(
                initial_section.get("positions") or {}, story_positions
            )

    # story position application moved to top-level helper

    # system prompt assembly uses global build_sys_prompt

    # Build actors from configs
    char_cfg = dict(characters or {})
    npcs_list: List[ReActAgent] = []  # legacy name; no longer used for turn order
    participants_order: List[AgentBase] = []
    actor_entries: Dict[str, dict] = {}
    try:
        actor_entries = {
            str(k): v
            for k, v in char_cfg.items()
            if isinstance(v, dict)
            and str(k) not in {"relations", "objective_positions", "participants"}
        }
    except Exception:
        actor_entries = {}
    # Map actor name -> type ("npc" or "player"); default to npc
    actor_types: Dict[str, str] = {}
    try:
        actor_types = {
            str(nm): str((entry or {}).get("type", "npc")).lower()
            for nm, entry in actor_entries.items()
        }
    except Exception:
        actor_types = {}
    # Participants resolution per request: derive purely from story positions that were ingested
    # into `story_positions` (supports top-level initial_positions/positions 或 initial.positions)。
    # If none present, run without participants (no implicit fallback to any default pair).
    allowed_names: List[str] = list(story_positions.keys())
    # Persist participants to world so all downstream consumers read from world only
    try:
        world.set_participants(allowed_names)
    except Exception:
        pass
    try:
        allowed_names_world: List[str] = list(
            world.snapshot().get("participants") or []
        )
    except Exception:
        allowed_names_world = list(allowed_names)
    allowed_names_str = ", ".join(allowed_names_world) if allowed_names_world else ""

    rel_cfg_raw = char_cfg.get("relations") if isinstance(char_cfg, dict) else {}

    def _relation_brief(name: str) -> str:
        """Build relation brief from world state, not raw config."""
        try:
            rel_map = dict(world.snapshot().get("relations") or {})
        except Exception:
            rel_map = {}
        if not rel_map:
            return ""
        me = str(name)
        entries: List[str] = []
        for key, raw in rel_map.items():
            try:
                a, b = key.split("->", 1)
            except Exception:
                continue
            if a != me or b == me:
                continue
            try:
                score = int(raw)
            except Exception:
                continue
            label = _relation_category(score)
            entries.append(f"{b}:{score:+d}（{label}）")
        return "；".join(entries)

    # Tool list must be provided by caller (main). Keep empty default.
    tool_list = list(tool_fns) if tool_fns is not None else []

    # Ensure character persona/appearance/quotes are stored in world for all actors
    try:
        for nm, entry in actor_entries.items():
            if not isinstance(entry, dict):
                continue
            try:
                world.set_character_meta(
                    nm,
                    persona=entry.get("persona"),
                    appearance=entry.get("appearance"),
                    quotes=entry.get("quotes"),
                )
            except Exception:
                pass
    except Exception:
        pass

    # Build agents for NPCs only; players由命令行输入驱动
    if allowed_names_world:
        for name in allowed_names_world:
            entry = (char_cfg.get(name) or {}) if isinstance(char_cfg, dict) else {}
            # Stat block: CoC only (DnD compatibility removed).
            try:
                coc_block = entry.get("coc")
                if isinstance(coc_block, dict):
                    world.set_coc_character_from_config(name=name, coc=coc_block or {})
                else:
                    # Create a minimal CoC sheet with mid-line defaults
                    world.set_coc_character(
                        name=name,
                        characteristics={
                            "STR": 50,
                            "DEX": 50,
                            "CON": 50,
                            "INT": 50,
                            "POW": 50,
                            "APP": 50,
                            "EDU": 60,
                            "SIZ": 50,
                            "LUCK": 50,
                        },
                    )
            except Exception:
                pass
            apply_story_position(world, story_positions, name)
            # Load inventory (weapons as items) from character config
            try:
                inv = entry.get("inventory") or {}
                if isinstance(inv, dict):
                    for it, cnt in inv.items():
                        try:
                            # Use world port instead of direct module to keep the engine decoupled
                            world.grant_item(target=name, item=str(it), n=int(cnt))
                        except Exception:
                            pass
            except Exception:
                pass

            # Build per-actor weapon brief for prompt
            def _weapon_brief_for(nm: str) -> str:
                try:
                    snap = world.snapshot()
                    wdefs = dict((snap.get("weapon_defs") or {}))
                    bag = dict((snap.get("inventory") or {}).get(str(nm), {}) or {})
                except Exception:
                    return "无"
                entries: List[str] = []
                for wid, count in bag.items():
                    if int(count) <= 0 or wid not in wdefs:
                        continue
                    wd = wdefs.get(wid) or {}
                    try:
                        rs = int(wd.get("reach_steps", 1))
                    except Exception:
                        rs = 1
                    dmg = wd.get("damage", "")
                    entries.append(f"{wid}(触及 {rs}步, 伤害 {dmg or '?'} )")
                return "；".join(entries) if entries else "无"

            # Read meta from world (single source of truth)
            try:
                sheet = (world.snapshot().get("characters") or {}).get(name, {}) or {}
            except Exception:
                sheet = {}
            persona = sheet.get("persona")
            if not isinstance(persona, str) or not persona.strip():
                raise ValueError(f"缺少角色人设(persona)：{name}")
            appearance = sheet.get("appearance")
            quotes = sheet.get("quotes")
            # Player 角色不创建 LLM agent；其对白来自命令行
            if str(actor_types.get(name, "npc")) == "player":
                # 不加入 participants_order（Hub 仅管理 NPC Agent 的内存）
                pass
            else:
                sys_prompt_text = build_sys_prompt(
                    name=name,
                    persona=persona,
                    appearance=appearance,
                    quotes=quotes,
                    relation_brief=relation_brief_for(world, name),
                    weapon_brief=weapon_brief_for(world, name),
                    allowed_names=allowed_names_str,
                )
                agent = build_agent(
                    name,
                    persona,
                    model_cfg,
                    sys_prompt=sys_prompt_text,
                    allowed_names=allowed_names_str,
                    appearance=appearance,
                    quotes=quotes,
                    relation_brief=relation_brief_for(world, name),
                    weapon_brief=weapon_brief_for(world, name),
                    tools=tool_list,
                )
                # 仅 NPC 参与 Hub 和初始化 pipeline
                npcs_list.append(agent)
                participants_order.append(agent)
        # preload non-participant actors (e.g., enemies) into world sheets
        for name, entry in actor_entries.items():
            if name in allowed_names_world:
                continue
            try:
                coc_block = entry.get("coc")
                if isinstance(coc_block, dict):
                    world.set_coc_character_from_config(name=name, coc=coc_block or {})
                else:
                    world.set_coc_character(
                        name=name,
                        characteristics={
                            "STR": 50,
                            "DEX": 50,
                            "CON": 50,
                            "INT": 50,
                            "POW": 50,
                            "APP": 50,
                            "EDU": 60,
                            "SIZ": 50,
                            "LUCK": 50,
                        },
                    )
            except Exception:
                pass
            apply_story_position(world, story_positions, name)
    # No fallback to default protagonists; if story provides no positions, run without participants.

    for nm in story_positions:
        try:
            if nm in (world.runtime().get("positions") or {}):
                continue
        except Exception:
            pass
        apply_story_position(world, story_positions, nm)

    # Initialize relations from config
    rel_cfg = rel_cfg_raw or {}
    if isinstance(rel_cfg, dict):
        for src, mapping in rel_cfg.items():
            if not isinstance(mapping, dict):
                continue
            for dst, val in mapping.items():
                try:
                    score = max(-100, min(100, int(val)))
                except Exception:
                    continue
                try:
                    world.set_relation(str(src), str(dst), score, reason="配置设定")
                except Exception:
                    pass

    # Scene setup from story config (moved to top-level helpers)
    scene_cfg = story_cfg.get("scene") if isinstance(story_cfg, dict) else {}
    scene_name, scene_objectives, scene_details, scene_weather, scene_time_min = (
        normalize_scene_cfg(scene_cfg)
    )
    if any(
        [
            scene_name,
            scene_objectives,
            scene_details,
            scene_weather,
            scene_time_min is not None,
        ]
    ):
        apply_scene_to_world(
            world,
            scene_name,
            scene_objectives,
            scene_details,
            scene_weather,
            scene_time_min,
        )

    current_round = 0

    def _emit(
        event_type: str,
        *,
        actor: Optional[str] = None,
        phase: Optional[str] = None,
        turn: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = dict(data or {})
        emit(
            event_type=event_type,
            actor=actor,
            phase=phase,
            turn=turn if turn is not None else (current_round or None),
            data=payload,
        )

    # Prepare per-run context for top-level helpers
    TOOL_DISPATCH = dict(tool_dispatch or {})
    allowed_set = {str(n) for n in (allowed_names_world or [])}
    CHAT_LOG: List[Dict[str, Any]] = []  # {actor, role, text, turn, phase}
    ACTION_LOG: List[Dict[str, Any]] = (
        []
    )  # {actor, tool, type, text|params, meta, turn}
    LAST_SEEN: Dict[str, int] = {}  # per-actor chat index checkpoint
    recap_enabled = True
    recap_msg_limit = DEFAULT_RECAP_MSG_LIMIT
    recap_action_limit = DEFAULT_RECAP_ACTION_LIMIT

    ctx = TurnContext(
        world=world,
        emit=_emit,
        tool_dispatch=TOOL_DISPATCH,
        tool_list=tool_list,
        chat_log=CHAT_LOG,
        action_log=ACTION_LOG,
        last_seen=LAST_SEEN,
        current_round=current_round,
        recap_enabled=recap_enabled,
        recap_msg_limit=recap_msg_limit,
        recap_action_limit=recap_action_limit,
        allowed_set=allowed_set,
        allowed_names_str=allowed_names_str,
        model_cfg=model_cfg,
        build_agent=build_agent,
        debug_dump_prompts=DEBUG_DUMP_PROMPTS,
    )

    # ---- In-memory mini logs for per-turn recap (kept in ctx) ----

    # Async CLI input helper (avoid blocking event loop when玩家发言)
    async def _async_input(prompt: str) -> str:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, lambda: input(prompt))
        except Exception:
            return ""

    # Dev context card removed; superseded by prompt logs

    # Tool call handling moved to top-level helper

    # Recap moved to top-level helper

    # --- State snapshot emitters (helper to reduce duplicate blocks) ---
    # Snapshot emitters moved to top-level helpers

    # --- Ephemeral NPC helper to remove duplication ---
    # Ephemeral agent, reach preview, and NPC turn helpers moved to top-level helpers

    # Fallback: 若上游未载入术式表，这里尝试一次惰性载入，避免术式提示/施放为“未知术式”
    try:
        if hasattr(world, "get_arts_defs") and callable(
            getattr(world, "get_arts_defs")
        ):
            arts_defs_now = world.get_arts_defs() or {}
            if not arts_defs_now and hasattr(world, "set_arts_defs"):
                try:
                    arts = load_arts() or {}
                except Exception:
                    arts = {}
                try:
                    world.set_arts_defs(arts)
                except Exception:
                    pass
    except Exception:
        pass

    # Human-readable header for participants and starting positions
    _start_pos_lines = []
    try:
        parts = list(world.snapshot().get("participants") or [])
        pos_map = world.snapshot().get("positions") or {}
        for nm in parts:
            pos = pos_map.get(nm) or story_positions.get(nm)
            if pos:
                _start_pos_lines.append(f"{nm}({pos[0]}, {pos[1]})")
    except Exception:
        _start_pos_lines = []
    _participants_header = (
        "参与者："
        + (
            ", ".join(world.snapshot().get("participants") or [])
            if (world.snapshot().get("participants") or [])
            else "(无)"
        )
        + (" | 初始坐标：" + "; ".join(_start_pos_lines) if _start_pos_lines else "")
    )

    # Opening text: read from configs, persist into world.scene_details (append) for single-source-of-truth
    opening_text: Optional[str] = None
    try:
        if isinstance(story_cfg, dict):
            sc = story_cfg.get("scene")
            if isinstance(sc, dict):
                txt = sc.get("description") or sc.get("opening") or ""
                if isinstance(txt, str) and txt.strip():
                    opening_text = txt.strip()
    except Exception:
        opening_text = None
    default_opening = "旧城区·北侧仓棚。铁梁回声震耳，每名战斗者都盯紧了自己的对手——退路已绝，只能分出胜负！"
    opening_line = opening_text or default_opening
    # Append opening into world.scene_details if not already present
    try:
        snap0 = world.snapshot()
        current_loc = str((snap0 or {}).get("location") or "")
        details0 = list((snap0 or {}).get("scene_details") or [])
        if opening_line and opening_line not in details0:
            details_new = details0 + [opening_line]
            world.set_scene(current_loc, None, append=True, details=details_new)
    except Exception as exc:
        _emit(
            "error",
            phase="scene",
            data={
                "message": "写入场景细节失败",
                "error_type": "scene_details_append",
                "exception": str(exc),
            },
        )
    announcement_text = opening_line + "\n" + _participants_header

    # 若无参与者（按 positions 推断）则在进入 Hub 前直接记录并结束
    if not allowed_names_world:
        try:
            _emit("state_update", phase="initial", data={"state": world.snapshot()})
        except Exception:
            pass
        try:
            _emit(
                "system",
                phase="system",
                data={"message": f"无参与者，自动结束。{_participants_header}"},
            )
        except Exception:
            pass
        try:
            _emit("state_update", phase="final", data={"state": world.snapshot()})
        except Exception:
            pass
        return

    # 在进入 Hub 和任何 NPC 开口之前，先广播一次完整快照，确保前端尽快拿到带坐标的状态
    try:
        _emit("state_update", phase="initial", data={"state": world.snapshot()})
    except Exception:
        pass
    # 无论入口是否走过 main()/server 的载表路径，这里追加一次术式表遥测，方便排查
    try:
        arts_defs = world.get_arts_defs() if hasattr(world, "get_arts_defs") else {}
        _emit(
            "system",
            phase="init",
            data={
                "message": "术式表载入完成",
                "arts_defs_count": len(arts_defs or {}),
                "arts_defs_keys": sorted(list((arts_defs or {}).keys())),
            },
        )
    except Exception:
        pass

    async with MsgHub(
        participants=list(participants_order),
        announcement=Msg(
            "Host",
            announcement_text,
            "assistant",
        ),
    ) as hub:
        # 开场：让每个 NPC 先各发一条对白（并可附带工具调用），以便在玩家输入前呈现剧情开端
        try:
            for name in list(allowed_names_world) or []:
                if str(actor_types.get(name, "npc")) != "npc":
                    continue
                try:
                    await npc_ephemeral_say(ctx, name, None, hub, recap_msg=None)
                except Exception:
                    pass
        except Exception:
            pass
        _emit("state_update", phase="initial", data={"state": world.snapshot()})
        round_idx = 1
        max_rounds = None

        def _objectives_resolved() -> bool:
            snap = world.snapshot()
            objs = list(snap.get("objectives") or [])
            if not objs:
                return False
            status = snap.get("objective_status") or {}
            for nm in objs:
                st = str(status.get(str(nm), "pending"))
                if st not in {"done", "blocked"}:
                    return False
            return True

        end_reason: Optional[str] = None
        # Default to original semantics: end when no hostiles (fixed behaviour)
        require_hostiles = True

        def _is_alive(nm: str) -> bool:
            try:
                chars = world.snapshot().get("characters", {}) or {}
                st = chars.get(str(nm), {})
                return int(st.get("hp", 1)) > 0
            except Exception:
                return True

        def _living_field_names() -> List[str]:
            # Prefer participants; else those with positions; else all characters
            base: List[str]
            if allowed_names_world:
                base = list(allowed_names_world)
            else:
                snap = world.snapshot()
                base = list((snap.get("positions") or {}).keys()) or list(
                    (snap.get("characters") or {}).keys()
                )
            return [n for n in base if _is_alive(n)]

        def _hostiles_present(threshold: int = -10) -> bool:
            names = _living_field_names()
            if len(names) <= 1:
                return False
            snap_rel = world.snapshot().get("relations") or {}
            for i, a in enumerate(names):
                for b in names[i + 1 :]:
                    try:
                        sc_ab = int(snap_rel.get(f"{str(a)}->{str(b)}", 0))
                    except Exception:
                        sc_ab = 0
                    try:
                        sc_ba = int(snap_rel.get(f"{str(b)}->{str(a)}", 0))
                    except Exception:
                        sc_ba = 0
                    if sc_ab <= threshold or sc_ba <= threshold:
                        return True
            return False

        while True:
            try:
                rt = world.runtime()
                hdr_round_val = int(rt.get("round") or round_idx)
                hdr_round = hdr_round_val if bool(rt.get("in_combat")) else round_idx
            except Exception:
                hdr_round = round_idx
            current_round = hdr_round
            # 压缩回合提示，避免冗长旁白
            ctx.current_round = current_round
            await bcast(
                ctx,
                hub,
                Msg("Host", f"第{hdr_round}回合", "assistant"),
                phase="round-start",
            )
            try:
                turn = world.get_turn()
                meta = turn.metadata or {}
                rnd = int(meta.get("round") or round_idx)
                if bool((world.runtime().get("in_combat"))):
                    current_round = rnd
            except Exception:
                pass

            emit_turn_state(ctx)
            emit_world_state(ctx, current_round)
            # 移除回合开始时的世界概要广播；仅在每个 NPC 行动前发送概要（见 context:world）

            # If无敌对，则退出战斗模式但不强制结束整体流程（除非显式要求）
            if not _hostiles_present():
                try:
                    if bool(world.runtime().get("in_combat")):
                        world.end_combat()
                except Exception:
                    pass
                if require_hostiles:
                    end_reason = "场上已无敌对存活单位"
                    break

            combat_cleared = False
            # 按参与者名称轮转；玩家与 NPC 均在其中
            for name in list(allowed_names_world) or []:
                name = str(name)
                # Skip turn only if the character is truly dead (hp<=0 and not in dying state)
                try:
                    sheet = (world.snapshot().get("characters") or {}).get(
                        name, {}
                    ) or {}
                    hpv = int(sheet.get("hp", 1))
                    dt = sheet.get("dying_turns_left", None)
                    is_dead = (hpv <= 0) and (dt is None)
                    if is_dead:
                        _emit(
                            "turn_start",
                            actor=name,
                            turn=current_round,
                            phase="actor-turn",
                            data={
                                "round": current_round,
                                "skipped": True,
                                "reason": "dead",
                            },
                        )
                        _emit(
                            "turn_end",
                            actor=name,
                            turn=current_round,
                            phase="actor-turn",
                            data={"round": current_round, "skipped": True},
                        )
                        continue
                except Exception:
                    pass

                try:
                    reset = world.reset_actor_turn(name)
                except Exception:
                    reset = None
                try:
                    st_meta = (reset.metadata or {}).get("state") if reset else None
                except Exception:
                    st_meta = None
                _emit(
                    "turn_start",
                    actor=name,
                    turn=current_round,
                    phase="actor-turn",
                    data={
                        "round": current_round,
                        "state": st_meta,
                    },
                )

                # Inject a recap message for all participants before the actor decides
                try:
                    # Dev-only context card removed; prompt logs replace it
                    # Also broadcast a fresh world summary right before decision,
                    # so each turn gets "世界概要 + 行动记忆 + 指导 prompt" together.
                    if CTX_BROADCAST_CONTEXT_TO_OBSERVERS:
                        try:
                            await bcast(
                                ctx,
                                hub,
                                Msg(
                                    "Host",
                                    _world_summary_text(world.snapshot()),
                                    "assistant",
                                ),
                                phase="context:world",
                            )
                        except Exception as exc:
                            # 记录世界概要渲染/广播失败，不中断回合
                            _emit(
                                "error",
                                phase="context:world",
                                data={
                                    "message": "世界概要广播失败",
                                    "error_type": "context_world_render",
                                    "exception": str(exc),
                                },
                            )
                        recap_msg = recap_for(ctx, name)
                        if recap_msg is not None:
                            await bcast(ctx, hub, recap_msg, phase="context:recap")
                    # 3: 不再注入“私人提示”到 agent 的内存，按你的选择仅使用 环境信息 + 场景回顾 作为上下文
                except Exception:
                    pass

                # Build per-turn private tip（仅 NPC 使用；玩家仅对白不走模型）
                # 1) Compute per-turn private section for this actor（回合资源 + 状态提示）
                private_section = None
                try:
                    snap_now = world.snapshot()
                    ch = (snap_now.get("characters") or {}).get(name, {}) or {}
                    ts_all = world.runtime().get("turn_state", {}) or {}
                    ts = ts_all.get(name, {}) or {}
                    # 优先处理对白（中性呈现）：取最近一条来自受控角色的对白（仅后端识别，不在文本中暴露身份）
                    lines_priv: List[str] = []
                    priority_msg = None  # (speaker, text)
                    try:
                        for e in reversed(CHAT_LOG):
                            sp = str(e.get("actor") or "")
                            if not sp or sp == "Host":
                                continue
                            if str(actor_types.get(sp, "npc")) == "player":
                                txtp = str(e.get("text") or "").strip()
                                if txtp:
                                    priority_msg = (sp, txtp)
                                    break
                    except Exception:
                        priority_msg = None
                    if priority_msg is not None:
                        sp, txtp = priority_msg
                        # 关系分值与类别（name -> sp）
                        try:
                            snap_rel = dict(snap_now.get("relations") or {})
                            sc = int(snap_rel.get(f"{name}->{sp}", 0))
                        except Exception:
                            sc = 0
                        try:
                            label = _relation_category(sc)
                        except Exception:
                            label = "中立"
                        lines_priv.append(PRIV_SPEECH_TITLE)
                        lines_priv.append(PRIV_SPEECH_SPEAKER.format(who=sp))
                        lines_priv.append(PRIV_SPEECH_CONTENT.format(text=txtp))
                        lines_priv.append(PRIV_SPEECH_REL.format(score=sc, label=label))
                    # 回合资源
                    try:
                        mv_left = int(ts.get("move_left", 0))
                    except Exception:
                        mv_left = 0
                    try:
                        mv_max = int(world.get_move_speed_steps(name))
                    except Exception:
                        mv_max = mv_left
                    action_used = bool(ts.get("action_used", False))
                    bonus_used = bool(ts.get("bonus_used", False))
                    reaction_avail = bool(ts.get("reaction_available", True))
                    lines_priv.append(PRIV_TURN_RES_TITLE)
                    lines_priv.append(f"- 移动：{mv_left}/{mv_max} 步")
                    lines_priv.append(
                        f"- 动作：{'可用' if not action_used else '已用'}；附赠动作：{'可用' if not bonus_used else '已用'}；反应：{'可用' if reaction_avail else '已用'}"
                    )
                    # Hard combat rules to avoid invalid attacks
                    lines_priv.append(REACH_RULE_LINE)
                    lines_priv.append(PRIV_VALID_ACTION_RULE_LINE)
                    # 异常状态提示（统一：一个标题 + 每个状态一行）
                    try:
                        sts = world.list_statuses(name) if hasattr(world, "list_statuses") else {}
                    except Exception:
                        sts = {}
                    statuses_lines: List[str] = []
                    # 如果统一状态表为空，但存在濒死倒计时字段，兜底构造一条 dying
                    dt = ch.get("dying_turns_left", None)
                    if not sts and dt is not None:
                        sts = {"dying": {"remaining": int(dt), "kind": "system"}}
                    hpv = ch.get("hp", None)
                    for k, info in (sts or {}).items():
                        key = str(k).lower()
                        try:
                            rem = info.get("remaining")
                            turns = (int(rem) if rem is not None else None)
                            duration_txt = (f"剩余 {turns} 回合" if turns is not None else "持续")
                        except Exception:
                            turns = None
                            duration_txt = "持续"
                        tmpl = PRIV_STATUS_LORE.get(key, PRIV_STATUS_LORE_DEFAULT)
                        # Format with safe fallbacks
                        line = tmpl.format(
                            state=str(k),
                            duration=duration_txt,
                            turns=(turns if turns is not None else 0),
                            hp=(hpv if hpv is not None else "?")
                        )
                        statuses_lines.append(line)
                    if statuses_lines:
                        lines_priv.append(PRIV_STATUS_TITLE)
                        lines_priv.extend(statuses_lines)

                    # 距离提示（仅当前行动者私有）：列出与场上所有单位的曼哈顿距离（步）
                    try:
                        positions = dict((snap_now.get("positions") or {}))
                        my_pos = positions.get(name)
                        # 候选单位：优先 participants；否则使用所有已登记坐标的单位
                        try:
                            participants_now = list(
                                world.snapshot().get("participants") or []
                            )
                        except Exception:
                            participants_now = []
                        candidates = [
                            n for n in (participants_now or list(positions.keys()))
                        ]
                        # 排除自己
                        candidates = [n for n in candidates if str(n) != str(name)]
                        lines_priv.append(PRIV_DIST_TITLE)
                        # 如果自身坐标缺失，给出说明
                        if not (isinstance(my_pos, (list, tuple)) and len(my_pos) >= 2):
                            lines_priv.append(PRIV_DIST_CANNOT_COMPUTE)
                        else:
                            mx, my = int(my_pos[0]), int(my_pos[1])
                            known: List[tuple[int, str]] = []
                            unknown: List[str] = []
                            for other in candidates:
                                pos = positions.get(other)
                                if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                                    try:
                                        ox, oy = int(pos[0]), int(pos[1])
                                        dist = abs(mx - ox) + abs(my - oy)
                                        known.append((int(dist), str(other)))
                                    except Exception:
                                        unknown.append(str(other))
                                else:
                                    unknown.append(str(other))
                            # 距离升序，名称次序作为平手兜底
                            known.sort(key=lambda t: (t[0], t[1]))
                            for dist, who in known:
                                lines_priv.append(
                                    PRIV_DIST_LINE.format(who=who, dist=int(dist))
                                )
                            for who in unknown:
                                lines_priv.append(
                                    PRIV_DIST_UNKNOWN_LINE.format(who=who)
                                )
                    except Exception:
                        # 容错：距离提示失败时跳过，不影响回合
                        pass
                    private_section = "\n".join(lines_priv)
                except Exception:
                    private_section = None

                # 2) 分支：player 走 CLI 输入；npc 走模型
                if str(actor_types.get(name, "npc")) == "player":
                    # 玩家发言：优先使用外部提供的异步输入通道（用于网页端），否则回退到 CLI 输入。
                    # 阻塞等待玩家输入，以保留“玩家优先发言”的体验（不自动跳过）。
                    try:
                        _emit(
                            "system",
                            actor=name,
                            phase="player_input",
                            data={"waiting": True},
                        )
                    except Exception:
                        pass
                    text_in = ""
                    if callable(player_input_provider):
                        try:
                            # 阻塞等待队列中提交的文本
                            text_in = str((await player_input_provider(name)) or "").strip()  # type: ignore[arg-type]
                        except Exception:
                            text_in = ""
                    else:
                        try:
                            text_in = (
                                await _async_input(f"[{name}] 请输入对白： ")
                            ).strip()
                        except Exception:
                            text_in = ""
                    if text_in:
                        # 玩家意图仅对该玩家角色可见：注入到本回合临时 agent 的私有系统提示中
                        private_lines = []
                        private_lines.append(PLAYER_CTRL_TITLE)
                        private_lines.append(PLAYER_CTRL_LINE.format(text=text_in))
                        private_section_pc = "\n".join(private_lines)

                        # 玩家角色也走一次临时 agent，由模型输出对白并执行工具（不广播原话）
                        await npc_ephemeral_say(
                            ctx, name, private_section_pc, hub, recap_msg
                        )
                else:
                    # 2a) NPC：构建一次性 agent（含本回私有提示），注入环境与回顾后输出对白+工具
                    await npc_ephemeral_say(ctx, name, private_section, hub, recap_msg)

                # Close player input prompt if any (frontend expects an explicit end signal)
                if str(actor_types.get(name, "npc")) == "player":
                    try:
                        _emit(
                            "system",
                            actor=name,
                            phase="player_input_end",
                            data={"waiting": False},
                        )
                    except Exception:
                        pass

                # End-of-turn: tick per-actor status timers and dying countdown
                # Note: world.tick_dying_for() also decrements CONTROL statuses even when not dying.
                # Calling it unconditionally ensures statuses like "silenced/rooted" expire correctly.
                try:
                    world.tick_dying_for(name)
                except Exception:
                    pass

                # After each action, if无敌对则退出战斗但继续对话流程
                if not _hostiles_present():
                    try:
                        if bool(world.runtime().get("in_combat")):
                            world.end_combat()
                    except Exception:
                        pass
                    if require_hostiles:
                        end_reason = "场上已无敌对存活单位"
                        combat_cleared = True
                        break
                _emit(
                    "turn_end",
                    actor=name,
                    turn=current_round,
                    phase="actor-turn",
                    data={"round": current_round},
                )

                # Soft pause: if a pause was requested, block here (between actors)
                if pause_gate is not None:
                    try:
                        await getattr(pause_gate, "wait_if_requested")(
                            after_actor=name, round_val=current_round
                        )
                    except Exception:
                        # Defensive: never break the loop due to pause gate errors
                        pass

            _emit(
                "turn_end",
                phase="round",
                turn=current_round,
                data={"round": current_round},
            )
            if combat_cleared:
                break
            round_idx += 1

            if _objectives_resolved():
                end_reason = "所有目标均已解决"
                break
            if max_rounds is not None and round_idx > max_rounds:
                end_reason = f"已达到最大回合 {max_rounds}"
                break

        final_snapshot = world.snapshot()
        _emit("state_update", phase="final", data={"state": final_snapshot})
        await bcast(
            ctx,
            hub,
            Msg(
                "Host",
                f"自动演算结束。{('(' + end_reason + ')') if end_reason else ''}",
                "assistant",
            ),
            phase="system",
        )


def _world_summary_text(snap: dict) -> str:
    try:
        t = int(snap.get("time_min", 0))
    except Exception:
        t = 0
    hh, mm = t // 60, t % 60
    weather = snap.get("weather", "unknown")
    location = snap.get("location", "未知")
    objectives = snap.get("objectives", []) or []
    obj_status = snap.get("objective_status", {}) or {}
    # Note: 为避免角色获悉他人物品，世界概要中不再包含任何“物品”信息
    positions = snap.get("positions", {}) or {}
    pos_lines = []
    try:
        for nm, coord in positions.items():
            if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                pos_lines.append(f"{nm}({coord[0]}, {coord[1]})")
    except Exception:
        pos_lines = []
    chars = snap.get("characters", {}) or {}
    char_lines = []
    try:
        for nm, st in chars.items():
            hp = st.get("hp")
            max_hp = st.get("max_hp")
            if hp is not None and max_hp is not None:
                extra = ""
                # Append dying turns-left or death marker if applicable
                try:
                    dt = st.get("dying_turns_left", None)
                    if dt is not None:
                        extra = f"（濒死{int(dt)}）"
                    elif int(hp) <= 0:
                        extra = "（死亡）"
                except Exception:
                    extra = extra
                char_lines.append(f"{nm}(HP {hp}/{max_hp}){extra}")
    except Exception:
        pass

    details = [
        d for d in (snap.get("scene_details") or []) if isinstance(d, str) and d.strip()
    ]
    lines = [
        WORLD_SUMMARY_HEADER.format(location=location, hh=hh, mm=mm, weather=weather),
        WORLD_SUMMARY_OBJECTIVES.format(
            objectives=(
                "; ".join(
                    (
                        f"{str(o)}({obj_status.get(str(o))})"
                        if obj_status.get(str(o))
                        else str(o)
                    )
                    for o in objectives
                )
                if objectives
                else "无"
            )
        ),
        # 说明：避免使用“系统提示”措辞以免模型联想出系统旁白；且不显示任何物品信息
        WORLD_SUMMARY_POSITIONS.format(
            positions=("; ".join(pos_lines) if pos_lines else "未记录")
        ),
        WORLD_SUMMARY_CHARACTERS.format(
            chars=("; ".join(char_lines) if char_lines else "未登记")
        ),
    ]
    if details:
        # Insert details after the header line
        lines.insert(1, WORLD_SUMMARY_DETAILS.format(details="；".join(details)))
    return "\n".join(lines)


def _bootstrap_runtime(
    *, for_server: bool = False, selected_story_id: Optional[str] = None
):
    # Load configs and create logging context, optionally reset world for server session
    model_cfg_obj = load_model_config()
    story_cfg = load_story_config(selected_story_id)
    characters = load_characters()
    weapons = load_weapons() or {}
    if is_dataclass(model_cfg_obj):
        model_cfg: Dict[str, Any] = asdict(model_cfg_obj)
    else:
        model_cfg = dict(getattr(model_cfg_obj, "__dict__", {}) or {})
    root = project_root()
    log_ctx = create_logging_context(base_path=root)
    if for_server:
        try:
            world_impl.reset_world()
        except Exception:
            pass
    world = _WorldPort()
    return model_cfg, story_cfg, characters, weapons, world, log_ctx, root


def main() -> None:
    print("============================================================")
    print("NPC Talk Demo (Orchestrator: main.py)")
    print("============================================================")

    # Bootstrap shared runtime bits
    model_cfg, story_cfg, characters, weapons, world, log_ctx, root = (
        _bootstrap_runtime(for_server=False)
    )

    # Clean prompt dumps at run start; keep only latest per actor during run
    try:
        prompts_dir = root / "logs" / "prompts"
        if prompts_dir.exists():
            for _p in prompts_dir.glob("*.txt"):
                try:
                    _p.unlink()
                except Exception:
                    pass
    except Exception:
        pass

    # Emit function adapter
    def emit(*, event_type: str, actor=None, phase=None, turn=None, data=None) -> None:
        ev = Event(
            event_type=EventType(event_type),
            actor=actor,
            phase=phase,
            turn=turn,
            data=dict(data or {}),
        )
        log_ctx.bus.publish(ev)

    # Load weapon table into world before tools are used
    try:
        world.set_weapon_defs(weapons)
    except Exception as exc:
        # 记录武器表载入失败，继续运行（允许无武器配置）
        emit(
            event_type="error",
            phase="init",
            data={
                "message": "加载武器表失败",
                "error_type": "weapon_defs_load",
                "exception": str(exc),
            },
        )
    # Load arts table (optional)
    try:
        arts = load_arts() or {}
        if hasattr(world, "set_arts_defs"):
            world.set_arts_defs(arts)
        # Structured telemetry: record how many arts were loaded and their ids
        try:
            emit(
                event_type="system",
                phase="init",
                data={
                    "message": "术式表载入完成",
                    "arts_defs_count": len(arts or {}),
                    "arts_defs_keys": sorted(list((arts or {}).keys())),
                },
            )
        except Exception:
            # logging should never block startup
            pass
    except Exception as exc:
        emit(
            event_type="error",
            phase="init",
            data={
                "message": "加载术式表失败",
                "error_type": "arts_defs_load",
                "exception": str(exc),
            },
        )
    # Inject the port (adapter) so actions depend on a stable surface
    tool_list, tool_dispatch = make_npc_actions(world=world)

    # Agent builder
    def build_agent(name, persona, model_cfg, **kwargs):
        return make_kimi_npc(name, persona, model_cfg, **kwargs)

    try:
        asyncio.run(
            run_demo(
                emit=emit,
                build_agent=build_agent,
                tool_fns=tool_list,
                tool_dispatch=tool_dispatch,
                # prompts removed
                model_cfg=model_cfg,
                story_cfg=story_cfg,
                characters=characters,
                world=world,
            )
        )
    except KeyboardInterrupt:
        pass
    finally:
        log_ctx.close()


import sys
import argparse
from collections import deque

# Optional server deps (only required in server mode)
try:  # lazy import to keep --once usable without extra deps
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
except Exception:  # pragma: no cover - defensive for environments without deps
    FastAPI = None  # type: ignore
    WebSocket = None  # type: ignore
    WebSocketDisconnect = Exception  # type: ignore
    JSONResponse = None  # type: ignore
    StaticFiles = None  # type: ignore
    CORSMiddleware = None  # type: ignore
    uvicorn = None  # type: ignore

# Use the same asyncio import consistently
import uuid as _uuid
from urllib.parse import parse_qs


class _EventBridge:
    """In-memory event buffer + websocket broadcaster.

    - Keeps a ring buffer of recent events for replay on reconnect.
    - Broadcasts every new event to connected WebSocket clients.
    """

    def __init__(self, maxlen: int = 2000) -> None:
        self._buf: deque[dict] = deque(maxlen=maxlen)
        self._clients: set = set()  # set[WebSocket]
        self._last_seq: int = 0
        self._lock = asyncio.Lock()

    @property
    def last_sequence(self) -> int:
        return self._last_seq

    async def clear(self) -> None:
        async with self._lock:
            self._buf.clear()
            self._last_seq = 0

    async def register(self, ws) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws) -> None:
        async with self._lock:
            self._clients.discard(ws)

    def replay_since(self, since: int) -> list[dict]:
        try:
            si = int(since or 0)
        except Exception:
            si = 0
        return [ev for ev in list(self._buf) if int(ev.get("sequence", 0) or 0) > si]

    async def on_event(self, event_dict: dict) -> None:
        # buffer
        try:
            seq = int(event_dict.get("sequence", 0) or 0)
        except Exception:
            seq = 0
        if not seq:
            seq = self._last_seq + 1
            event_dict["sequence"] = seq
        self._last_seq = max(self._last_seq, seq)
        self._buf.append(event_dict)
        # broadcast
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json({"type": "event", "event": event_dict})
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                await self.unregister(ws)
            except Exception:
                pass

    async def send_control(self, typ: str, payload: dict | None = None) -> None:
        """Broadcast a control message to all clients (non-event)."""
        dead = []
        msg = {"type": str(typ)}
        if payload:
            try:
                msg.update(dict(payload))
            except Exception:
                pass
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                await self.unregister(ws)
            except Exception:
                pass

    async def send_end(self) -> None:
        await self.send_control("end")


class _ServerState:
    def __init__(self) -> None:
        self.task: Optional[asyncio.Task] = None
        self.running: bool = False
        self.bridge = _EventBridge()
        self.last_snapshot: Dict[str, Any] = {}
        self.session_id: str = ""
        self.log_ctx = None  # LoggingContext
        self.player_queues: Dict[str, asyncio.Queue[str]] = {}
        # runtime-only selected story id (not persisted)
        self.selected_story_id: str = ""
        # soft-pause gate (initialized when a session starts)
        self.pause_gate: Optional["PauseGate"] = None

    def is_running(self) -> bool:
        return bool(self.task) and not bool(self.task.done()) and self.running

    def get_player_queue(self, name: str) -> asyncio.Queue[str]:
        q = self.player_queues.get(name)
        if q is None:
            q = asyncio.Queue()
            self.player_queues[name] = q
        return q


_STATE = _ServerState()

# Multi-session (per-page) support. Each SID maps to an independent _ServerState.
_SESSIONS: Dict[str, _ServerState] = {}
_SESSIONS_LOCK = Lock()


def _get_session(sid: Optional[str]) -> _ServerState:
    if not sid:
        return _STATE  # backward-compat: no SID -> legacy singleton
    with _SESSIONS_LOCK:
        st = _SESSIONS.get(sid)
        if st is None:
            st = _ServerState()
            _SESSIONS[sid] = st
        return st


def _parse_sid_from(req: Any) -> Optional[str]:
    sid = None
    # Try HTTP header first (REST)
    try:
        hdrs = getattr(req, "headers", None)
        if hdrs:
            sid = str(hdrs.get("X-Session-ID") or "").strip()
    except Exception:
        sid = None
    # Fallback to query string (?sid=...); works for WebSocket
    if not sid:
        try:
            scope = getattr(req, "scope", None) or {}
            raw_qs = scope.get("query_string", b"") or b""
            qs = parse_qs(raw_qs.decode("utf-8")) if raw_qs else {}
            sid = (qs.get("sid", [""])[0] or "").strip()
        except Exception:
            sid = None
    return sid or None


class PauseGate:
    """Cooperative soft-pause gate.

    - request(): mark a pause request. The game keeps running until it reaches
      the next safe point and calls wait_if_requested().
    - wait_if_requested(): when called at a safe point, transition into paused
      state, broadcast a control message, and block until resumed.
    - resume(): clear request and paused state, unblock waiters and broadcast.

    Designed so that clicking "终止" does NOT interrupt the current actor's
    output; the pause takes effect between turns.
    """

    def __init__(self) -> None:
        self.requested: bool = False
        self.paused: bool = False
        self._resume_ev: asyncio.Event = asyncio.Event()
        self._resume_ev.set()  # not paused initially
        # Optional async callbacks provided by server to notify clients
        self.on_paused: Optional[Callable[[dict], Awaitable[None]]] = None
        self.on_resumed: Optional[Callable[[], Awaitable[None]]] = None

    def request(self) -> None:
        self.requested = True

    def is_paused_or_requested(self) -> bool:
        return bool(self.requested or self.paused)

    async def wait_if_requested(
        self, *, after_actor: Optional[str] = None, round_val: Optional[int] = None
    ) -> None:
        if not self.requested:
            return
        # Enter paused state at the first safe point we encounter
        if not self.paused:
            self.paused = True
            try:
                self._resume_ev.clear()
            except Exception:
                pass
            cb = self.on_paused
            if callable(cb):
                try:
                    payload = {"after_actor": after_actor, "round": round_val}
                    await cb(payload)
                except Exception:
                    pass
        # Block until resumed
        await self._resume_ev.wait()

    async def resume(self) -> None:
        # Clear request; if currently paused, flip state and notify
        self.requested = False
        was_paused = self.paused
        self.paused = False
        try:
            self._resume_ev.set()
        except Exception:
            pass
        cb = self.on_resumed
        if was_paused and callable(cb):
            try:
                await cb()
            except Exception:
                pass


async def _start_game_server_mode(
    *, selected_story_id: Optional[str] = None
) -> Tuple[bool, str]:
    """Start one game run in background if not already running."""
    if _STATE.is_running():
        return True, "already running"

    # Bootstrap shared bits; in server we reset world to avoid inheriting state
    # If caller provided an explicit selection, remember it (runtime only)
    if selected_story_id:
        try:
            _STATE.selected_story_id = str(selected_story_id)
        except Exception:
            pass

    model_cfg, story_cfg, characters, weapons, world, log_ctx, root = (
        _bootstrap_runtime(
            for_server=True,
            selected_story_id=_STATE.selected_story_id or None,
        )
    )
    _STATE.log_ctx = log_ctx
    try:
        world.set_weapon_defs(weapons)
    except Exception as exc:
        # record error to structured logs but do not fail start
        try:
            ev = Event(
                event_type=EventType.ERROR,
                data={
                    "message": "加载武器表失败",
                    "error_type": "weapon_defs_load",
                    "exception": str(exc),
                },
            )
            log_ctx.bus.publish(ev)
        except Exception:
            pass

    # Load arts table (optional, server mode)
    try:
        arts = load_arts() or {}
        if hasattr(world, "set_arts_defs"):
            world.set_arts_defs(arts)
        # Structured telemetry (server mode): record arts load result
        try:
            ev = Event(
                event_type=EventType.SYSTEM,
                data={
                    "message": "术式表载入完成",
                    "arts_defs_count": len(arts or {}),
                    "arts_defs_keys": sorted(list((arts or {}).keys())),
                },
            )
            log_ctx.bus.publish(ev)
        except Exception:
            pass
    except Exception as exc:
        try:
            ev = Event(
                event_type=EventType.ERROR,
                data={
                    "message": "加载术式表失败",
                    "error_type": "arts_defs_load",
                    "exception": str(exc),
                },
            )
            log_ctx.bus.publish(ev)
        except Exception:
            pass

    tool_list, tool_dispatch = make_npc_actions(world=world)

    # New session id for correlation
    _STATE.session_id = str(_uuid.uuid4())
    # Initialize soft-pause gate for this session
    gate = PauseGate()
    _STATE.pause_gate = gate

    def emit(*, event_type: str, actor=None, phase=None, turn=None, data=None) -> None:
        ev = Event(
            event_type=EventType(event_type),
            actor=actor,
            phase=phase,
            turn=turn,
            data=dict(data or {}),
        )
        ev.correlation_id = _STATE.session_id
        # 1) structured/story logs
        try:
            published = log_ctx.bus.publish(ev)
        except Exception:
            published = None
        # 2) WS broadcast with the normalised dict (sequence/timestamp assigned by bus)
        try:
            payload = (
                published.to_dict() if published else ev.to_dict()
            )  # ev may lack seq/timestamp
            asyncio.create_task(_STATE.bridge.on_event(payload))
        except Exception:
            pass
        # 3) snapshot cache
        if event_type == "state_update":
            try:
                _STATE.last_snapshot = dict((data or {}).get("state") or {})
            except Exception:
                pass

    def build_agent(name, persona, model_cfg, **kwargs):
        return make_kimi_npc(name, persona, model_cfg, **kwargs)

    async def _runner() -> None:
        try:
            _STATE.running = True
            # reset event buffer for new session
            await _STATE.bridge.clear()
            # Pre-populate world & snapshot from story config so hello has positions
            try:
                story_positions: Dict[str, Tuple[int, int]] = {}

                def _ingest_positions(raw: Any) -> None:
                    if not isinstance(raw, dict):
                        return
                    for actor_name, pos in raw.items():
                        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                            try:
                                story_positions[str(actor_name)] = (
                                    int(pos[0]),
                                    int(pos[1]),
                                )
                            except Exception:
                                continue

                if isinstance(story_cfg, dict):
                    _ingest_positions(story_cfg.get("initial_positions") or {})
                    _ingest_positions(story_cfg.get("positions") or {})
                    initial_section = story_cfg.get("initial")
                    if isinstance(initial_section, dict):
                        _ingest_positions(initial_section.get("positions") or {})
                # apply into world before first snapshot
                for nm, (x, y) in story_positions.items():
                    try:
                        world.set_position(nm, x, y)
                    except Exception:
                        pass
                if story_positions:
                    try:
                        world.set_participants(list(story_positions.keys()))
                    except Exception:
                        pass
                # snapshot after pre-population
                try:
                    _STATE.last_snapshot = world.snapshot()
                except Exception:
                    _STATE.last_snapshot = {}
            except Exception:
                try:
                    _STATE.last_snapshot = world.snapshot()
                except Exception:
                    _STATE.last_snapshot = {}
            # Clean prompt dumps at session start; keep only latest per actor during run
            try:
                prompts_dir = root / "logs" / "prompts"
                if prompts_dir.exists():
                    for _p in prompts_dir.glob("*.txt"):
                        try:
                            _p.unlink()
                        except Exception:
                            pass
            except Exception:
                pass

            # Bind pause/resume hooks to broadcast control messages
            async def _on_paused(payload: dict) -> None:
                try:
                    await _STATE.bridge.send_control("paused", payload)
                except Exception:
                    pass

            async def _on_resumed() -> None:
                try:
                    await _STATE.bridge.send_control("resumed")
                except Exception:
                    pass

            gate.on_paused = _on_paused
            gate.on_resumed = _on_resumed

            await run_demo(
                emit=emit,
                build_agent=build_agent,
                tool_fns=tool_list,
                tool_dispatch=tool_dispatch,
                model_cfg=model_cfg,
                story_cfg=story_cfg,
                characters=characters,
                world=world,
                player_input_provider=lambda actor_name: _STATE.get_player_queue(
                    str(actor_name)
                ).get(),
                pause_gate=gate,
            )
        except Exception as exc:
            # Emit a terminal error event
            try:
                err = Event(
                    event_type=EventType.ERROR,
                    phase="final",
                    data={"message": f"runtime error: {exc}"},
                )
                log_ctx.bus.publish(err)
                asyncio.create_task(_STATE.bridge.on_event(err.to_dict()))
            except Exception:
                pass
        finally:
            _STATE.running = False
            try:
                # friendly end marker for clients
                end_seq = _STATE.bridge.last_sequence + 1
                asyncio.create_task(
                    _STATE.bridge.on_event(
                        {
                            "event_id": f"END-{_STATE.session_id}",
                            "sequence": end_seq,
                            "timestamp": "",
                            "event_type": "system",
                            "phase": "final",
                            "data": {"message": "game finished"},
                            "correlation_id": _STATE.session_id,
                        }
                    )
                )
            except Exception:
                pass
            try:
                asyncio.create_task(_STATE.bridge.send_end())
            except Exception:
                pass
            try:
                log_ctx.close()
            except Exception:
                pass

            _STATE.log_ctx = None

    _STATE.task = asyncio.create_task(_runner())
    await asyncio.sleep(0)
    return True, "started"


async def _stop_game_server_mode() -> Tuple[bool, str]:
    """Hard stop current session (used by restart)."""
    # Idempotent stop: return success when not running; ensure cleanup completes when running.
    if not _STATE.is_running():
        try:
            # Friendly end marker for any stale clients
            await _STATE.bridge.send_end()
        except Exception:
            pass
        return True, "not running"
    try:
        _STATE.task.cancel()  # cooperative cancellation
    except Exception:
        pass
    # Await the cancelled task to let its cleanup run; suppress CancelledError
    if _STATE.task is not None:
        try:
            await _STATE.task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            _STATE.task = None
    try:
        await _STATE.bridge.send_end()
    except Exception:
        pass
    return True, "stopped"


# Multi-session variants operating on a specific _ServerState
async def _start_game_for(
    state: _ServerState, *, selected_story_id: Optional[str] = None
) -> Tuple[bool, str]:
    if state.is_running():
        return True, "already running"

    if selected_story_id:
        try:
            state.selected_story_id = str(selected_story_id)
        except Exception:
            pass

    model_cfg, story_cfg, characters, weapons, world, log_ctx, root = (
        _bootstrap_runtime(
            for_server=True,
            selected_story_id=state.selected_story_id or None,
        )
    )
    state.log_ctx = log_ctx
    try:
        world.set_weapon_defs(weapons)
    except Exception as exc:
        try:
            ev = Event(
                event_type=EventType.ERROR,
                data={
                    "message": "加载武器表失败",
                    "error_type": "weapon_defs_load",
                    "exception": str(exc),
                },
            )
            log_ctx.bus.publish(ev)
        except Exception:
            pass

    tool_list, tool_dispatch = make_npc_actions(world=world)

    state.session_id = str(_uuid.uuid4())
    gate = PauseGate()
    state.pause_gate = gate

    def emit(*, event_type: str, actor=None, phase=None, turn=None, data=None) -> None:
        ev = Event(
            event_type=EventType(event_type),
            actor=actor,
            phase=phase,
            turn=turn,
            data=dict(data or {}),
        )
        ev.correlation_id = state.session_id
        try:
            published = log_ctx.bus.publish(ev)
        except Exception:
            published = None
        try:
            payload = published.to_dict() if published else ev.to_dict()
            asyncio.create_task(state.bridge.on_event(payload))
        except Exception:
            pass
        if event_type == "state_update":
            try:
                state.last_snapshot = dict((data or {}).get("state") or {})
            except Exception:
                pass

    def build_agent(name, persona, model_cfg, **kwargs):
        return make_kimi_npc(name, persona, model_cfg, **kwargs)

    async def _runner() -> None:
        try:
            state.running = True
            await state.bridge.clear()
            try:
                story_positions: Dict[str, Tuple[int, int]] = {}

                def _ingest_positions(raw: Any) -> None:
                    if not isinstance(raw, dict):
                        return
                    for actor_name, pos in raw.items():
                        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                            try:
                                story_positions[str(actor_name)] = (
                                    int(pos[0]),
                                    int(pos[1]),
                                )
                            except Exception:
                                continue

                if isinstance(story_cfg, dict):
                    _ingest_positions(story_cfg.get("initial_positions") or {})
                    _ingest_positions(story_cfg.get("positions") or {})
                    initial_section = story_cfg.get("initial")
                    if isinstance(initial_section, dict):
                        _ingest_positions(initial_section.get("positions") or {})
                for nm, (x, y) in story_positions.items():
                    try:
                        world.set_position(nm, x, y)
                    except Exception:
                        pass
                if story_positions:
                    try:
                        world.set_participants(list(story_positions.keys()))
                    except Exception:
                        pass
                try:
                    state.last_snapshot = world.snapshot()
                except Exception:
                    state.last_snapshot = {}
            except Exception:
                try:
                    state.last_snapshot = world.snapshot()
                except Exception:
                    state.last_snapshot = {}

            # Clean prompt dumps at session start; keep only latest per actor during run
            try:
                prompts_dir = root / "logs" / "prompts"
                if prompts_dir.exists():
                    for _p in prompts_dir.glob("*.txt"):
                        try:
                            _p.unlink()
                        except Exception:
                            pass
            except Exception:
                pass

            async def _on_paused(payload: dict) -> None:
                try:
                    await state.bridge.send_control("paused", payload)
                except Exception:
                    pass

            async def _on_resumed() -> None:
                try:
                    await state.bridge.send_control("resumed")
                except Exception:
                    pass

            gate.on_paused = _on_paused
            gate.on_resumed = _on_resumed

            await run_demo(
                emit=emit,
                build_agent=build_agent,
                tool_fns=tool_list,
                tool_dispatch=tool_dispatch,
                model_cfg=model_cfg,
                story_cfg=story_cfg,
                characters=characters,
                world=world,
                player_input_provider=lambda actor_name: state.get_player_queue(
                    str(actor_name)
                ).get(),
                pause_gate=gate,
            )
        except Exception as exc:
            try:
                err = Event(
                    event_type=EventType.ERROR,
                    phase="final",
                    data={"message": f"runtime error: {exc}"},
                )
                log_ctx.bus.publish(err)
                asyncio.create_task(state.bridge.on_event(err.to_dict()))
            except Exception:
                pass
        finally:
            state.running = False
            try:
                end_seq = state.bridge.last_sequence + 1
                asyncio.create_task(
                    state.bridge.on_event(
                        {
                            "event_id": f"END-{state.session_id}",
                            "sequence": end_seq,
                            "timestamp": "",
                            "event_type": "system",
                            "phase": "final",
                            "data": {"message": "game finished"},
                            "correlation_id": state.session_id,
                        }
                    )
                )
            except Exception:
                pass
            try:
                asyncio.create_task(state.bridge.send_end())
            except Exception:
                pass
            try:
                log_ctx.close()
            except Exception:
                pass
            state.log_ctx = None

    state.task = asyncio.create_task(_runner())
    await asyncio.sleep(0)
    return True, "started"


async def _stop_game_for(state: _ServerState) -> Tuple[bool, str]:
    if not state.is_running():
        try:
            await state.bridge.send_end()
        except Exception:
            pass
        return True, "not running"
    try:
        state.task.cancel()
    except Exception:
        pass
    if state.task is not None:
        try:
            await state.task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            state.task = None
    try:
        await state.bridge.send_end()
    except Exception:
        pass
    return True, "stopped"


def _make_app(web_dir: Optional[Path], *, allow_cors_from: Optional[list[str]] = None):
    if FastAPI is None or uvicorn is None:
        raise RuntimeError(
            "FastAPI/uvicorn not installed. Install fastapi and uvicorn[standard]."
        )
    app = FastAPI()

    # CORS if requested (for cross-origin frontends like dev servers)
    if allow_cors_from and CORSMiddleware is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_cors_from,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/healthz")
    async def _healthz():
        return {"ok": True}

    # --- Simple config editor endpoints (story/characters/weapons) ---
    # These endpoints enable the built-in settings editor (bottom drawer) to
    # fetch and persist JSON configs safely without restarting automatically.
    cfg_dir = project_root() / "configs"

    def _cfg_path(name: str) -> Path:
        m = {
            "story": cfg_dir / "story.json",
            "characters": cfg_dir / "characters.json",
            "weapons": cfg_dir / "weapons.json",
        }
        if name not in m:
            raise KeyError(f"unsupported config: {name}")
        return m[name]

    def _json_load_text(p: Path) -> dict:
        # no fallback: read and propagate errors
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _validate_story(obj: dict) -> tuple[bool, str]:
        """Validate story config.

        Accept either a single-story object, or a multi-story container:
          {"stories": {"id": {...}, ...}}
        """

        def _validate_one(story: dict) -> tuple[bool, str]:
            if not isinstance(story, dict):
                return False, "story must be a JSON object"
            scene = story.get("scene")
            if scene is not None and not isinstance(scene, dict):
                return False, "scene must be object when provided"
            if isinstance(scene, dict):
                # details: list of strings
                det = scene.get("details")
                if det is not None:
                    if not isinstance(det, list) or not all(
                        isinstance(x, str) for x in det
                    ):
                        return False, "scene.details must be an array of strings"
                # objectives: list of strings
                objs = scene.get("objectives")
                if objs is not None:
                    if not isinstance(objs, list) or not all(
                        isinstance(x, str) for x in objs
                    ):
                        return False, "scene.objectives must be an array of strings"
            # initial_positions: { name: [x,y] }
            ip = story.get("initial_positions")
            if ip is not None:
                if not isinstance(ip, dict):
                    return False, "initial_positions must be an object"
                for k, v in ip.items():
                    if not (isinstance(v, (list, tuple)) and len(v) >= 2):
                        return False, f"initial_positions.{k} must be [x,y]"
                    try:
                        int(v[0])
                        int(v[1])
                    except Exception:
                        return (
                            False,
                            f"initial_positions.{k} coordinates must be integers",
                        )
            return True, "ok"

        if not isinstance(obj, dict):
            return False, "story must be a JSON object"
        # Hard-delete policy: top-level active_id is not supported
        if "active_id" in obj:
            return False, "active_id is not supported"
        # Multi-story container
        if isinstance(obj.get("stories"), dict):
            stories = obj.get("stories") or {}
            for sid, s in stories.items():
                ok, msg = _validate_one(s)
                if not ok:
                    return False, f"story '{sid}' invalid: {msg}"
            return True, "ok"
        # Single-story legacy
        return _validate_one(obj)

    def _validate_weapons(obj: dict) -> tuple[bool, str]:
        if not isinstance(obj, dict):
            return False, "weapons must be an object"
        allowed = {
            "label",
            "reach_steps",
            "skill",
            "defense_skill",
            "damage",
            "damage_type",
        }
        for wid, w in obj.items():
            if not isinstance(w, dict):
                return False, f"weapon {wid} must be an object"
            extra = set(w.keys()) - allowed
            if extra:
                return False, f"weapon {wid} has unknown keys: {sorted(extra)}"
            for req in (
                "label",
                "reach_steps",
                "skill",
                "defense_skill",
                "damage",
                "damage_type",
            ):
                if req not in w:
                    return False, f"weapon {wid} missing required field '{req}'"
            try:
                rs = int(w.get("reach_steps"))
                if rs <= 0:
                    return False, f"weapon {wid}.reach_steps must be > 0"
            except Exception:
                return False, f"weapon {wid}.reach_steps must be an integer"
            dmg = str(w.get("damage") or "").lower()
            import re as _re

            if not _re.fullmatch(r"\d*d\d+(?:[+-]\d+)?", dmg):
                return False, f"weapon {wid}.damage must be NdM[+/-K], got '{dmg}'"
        return True, "ok"

    def _validate_characters(obj: dict) -> tuple[bool, str]:
        if not isinstance(obj, dict):
            return False, "characters must be an object"
        # Loose validation: allow any keys except legacy 'dnd' which is no longer supported
        for nm, data in obj.items():
            if nm == "relations":
                # relations is object of name -> name -> int
                rel = data
                if not isinstance(rel, dict):
                    return False, "relations must be an object"
                for a, m in rel.items():
                    if not isinstance(m, dict):
                        return False, f"relations.{a} must be an object"
                    for b, val in m.items():
                        try:
                            int(val)
                        except Exception:
                            return False, f"relations.{a}.{b} must be integer"
                continue
            if not isinstance(data, dict):
                return False, f"character {nm} must be an object"
            if "dnd" in data:
                return (
                    False,
                    f"character {nm}: 'dnd' block is not supported (use 'coc')",
                )
        return True, "ok"

    def _atomic_write(path: Path, obj: dict) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        # ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        tmp.replace(path)

    @app.get("/api/config/{name}")
    async def api_get_config(name: str):  # type: ignore[no-redef]
        try:
            p = _cfg_path(str(name))
        except KeyError:
            return JSONResponse(
                {"ok": False, "message": f"unsupported config: {name}"}, status_code=404
            )
        data = _json_load_text(p)
        # Hard delete policy: never expose legacy active_id back to clients
        if str(name) == "story" and isinstance(data, dict) and ("active_id" in data):
            try:
                data = dict(data)
                data.pop("active_id", None)
            except Exception:
                pass
        return {"ok": True, "name": name, "data": data}

    @app.post("/api/config/{name}")
    async def api_set_config(name: str, payload: dict):  # type: ignore[no-redef]
        name = str(name)
        try:
            p = _cfg_path(name)
        except KeyError:
            return JSONResponse(
                {"ok": False, "message": f"unsupported config: {name}"}, status_code=404
            )
        data = dict(payload or {})
        # Hard delete: reject legacy active_id even if provided
        if name == "story" and isinstance(data, dict) and ("active_id" in data):
            return JSONResponse(
                {"ok": False, "message": "active_id is not supported"}, status_code=422
            )
        # validate by type
        ok = True
        msg = "ok"
        if name == "story":
            ok, msg = _validate_story(data)
        elif name == "weapons":
            ok, msg = _validate_weapons(data)
        elif name == "characters":
            ok, msg = _validate_characters(data)
        else:
            ok = False
            msg = "unsupported config"
        if not ok:
            return JSONResponse({"ok": False, "message": msg}, status_code=422)
        try:
            _atomic_write(p, data)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "message": f"write failed: {exc}"}, status_code=500
            )
        return {"ok": True}

    # Helper: list available story ids from config (container -> keys; single -> ['default'] or [])
    def _list_story_ids() -> list[str]:
        ids: list[str] = []
        try:
            d = _json_load_text(_cfg_path("story"))
            if isinstance(d, dict):
                if isinstance(d.get("stories"), dict):
                    ids = sorted(list((d.get("stories") or {}).keys()))
                else:
                    # Treat any non-container dict (including empty {}) as single-story legacy -> 'default'
                    ids = ["default"]
        except Exception:
            # keep empty on read error
            ids = []
        return ids

    @app.get("/api/stories")
    async def api_stories(request: Request):  # type: ignore[no-redef]
        ids = _list_story_ids()
        st = _get_session(_parse_sid_from(request))
        sel = (
            st.selected_story_id
            if st.selected_story_id in ids
            else (ids[0] if ids else "")
        )
        return {"ok": True, "ids": ids, "selected": sel}

    @app.post("/api/select_story")
    async def api_select_story(payload: dict, request: Request):  # type: ignore[no-redef]
        try:
            sid = str(payload.get("id") or "").strip()
        except Exception:
            return JSONResponse(
                {"ok": False, "message": "invalid payload"}, status_code=400
            )
        ids = _list_story_ids()
        if not sid or sid not in ids:
            return JSONResponse(
                {"ok": False, "message": "unknown story id"}, status_code=400
            )
        st = _get_session(_parse_sid_from(request))
        st.selected_story_id = sid
        return {"ok": True, "selected": sid}

    @app.get("/api/preview_state")
    async def api_preview_state(id: Optional[str] = None, request: Request = None):  # type: ignore[no-redef]
        """Return a preview world snapshot for the selected story without starting a session.

        Query: ?id=<story_id> (optional). If omitted, use current session's selected_story_id;
        if still empty, fallback to the first available id.
        """
        # Resolve story id
        ids = _list_story_ids()
        if not ids:
            return JSONResponse(
                {"ok": False, "message": "no stories available"}, status_code=404
            )
        sid = None
        try:
            sid = str(id or "").strip() or None
        except Exception:
            sid = None
        if not sid and request is not None:
            st = _get_session(_parse_sid_from(request))
            if st.selected_story_id in ids:
                sid = st.selected_story_id
        if not sid:
            sid = ids[0]
        if sid not in ids:
            return JSONResponse(
                {"ok": False, "message": "unknown story id"}, status_code=400
            )

        # Build a fresh world snapshot mirroring server bootstrap logic, but without running the game loop
        try:
            model_cfg, story_cfg, characters, weapons, world, log_ctx, root = (
                _bootstrap_runtime(
                    for_server=True,
                    selected_story_id=sid,
                )
            )
            # Make weapon defs available to snapshot consumers
            try:
                world.set_weapon_defs(weapons)
            except Exception:
                pass
            try:
                arts = load_arts() or {}
                if hasattr(world, "set_arts_defs"):
                    world.set_arts_defs(arts)
            except Exception:
                pass

            # Ingest starting positions from story config (supports initial_positions/positions and initial.positions)
            story_positions: Dict[str, Tuple[int, int]] = {}
            try:
                _parse_story_positions(
                    story_cfg.get("initial_positions") or {}, story_positions
                )
            except Exception:
                pass
            try:
                _parse_story_positions(
                    story_cfg.get("positions") or {}, story_positions
                )
            except Exception:
                pass
            try:
                initial_section = story_cfg.get("initial")
                if isinstance(initial_section, dict):
                    _parse_story_positions(
                        initial_section.get("positions") or {}, story_positions
                    )
            except Exception:
                pass
            for nm, (x, y) in story_positions.items():
                try:
                    world.set_position(nm, x, y)
                except Exception:
                    pass
            if story_positions:
                try:
                    world.set_participants(list(story_positions.keys()))
                except Exception:
                    pass

            # Apply scene fields (name/objectives/details/weather/time)
            try:
                scene_cfg = (
                    story_cfg.get("scene") if isinstance(story_cfg, dict) else {}
                )
                (
                    scene_name,
                    scene_objectives,
                    scene_details,
                    scene_weather,
                    scene_time_min,
                ) = normalize_scene_cfg(scene_cfg)
                if any(
                    [
                        scene_name,
                        scene_objectives,
                        scene_details,
                        scene_weather,
                        scene_time_min is not None,
                    ]
                ):
                    apply_scene_to_world(
                        world,
                        scene_name,
                        scene_objectives,
                        scene_details,
                        scene_weather,
                        scene_time_min,
                    )
            except Exception:
                pass

            snap = world.snapshot()
            return {"ok": True, "selected": sid, "state": snap}
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "message": f"preview failed: {exc}"}, status_code=500
            )

    @app.post("/api/start")
    async def api_start(payload: dict | None = None, request: Request = None):  # type: ignore[no-redef]
        # When already running and a soft-pause is in effect, this acts as Resume
        st = _get_session(_parse_sid_from(request)) if request is not None else _STATE
        if st.is_running() and st.pause_gate and st.pause_gate.is_paused_or_requested():
            try:
                await st.pause_gate.resume()
            except Exception:
                return JSONResponse(
                    {"ok": False, "message": "resume failed"}, status_code=500
                )
            return JSONResponse(
                {"ok": True, "message": "resumed", "session_id": st.session_id}
            )
        # Otherwise, normal start semantics
        sid = None
        try:
            sid = str((payload or {}).get("story_id") or "").strip()
        except Exception:
            sid = None
        if sid:
            ids = _list_story_ids()
            if sid not in ids:
                return JSONResponse(
                    {"ok": False, "message": "unknown story id"}, status_code=400
                )
            st.selected_story_id = sid
        ok, msg = await _start_game_for(
            st, selected_story_id=st.selected_story_id or None
        )
        code = 200 if ok else 409
        return JSONResponse(
            {"ok": ok, "message": msg, "session_id": st.session_id}, status_code=code
        )

    @app.post("/api/stop")
    async def api_stop(request: Request):  # type: ignore[no-redef]
        # Soft pause request (do not cancel the running task).
        st = _get_session(_parse_sid_from(request))
        if st.is_running():
            if st.pause_gate is None:
                st.pause_gate = PauseGate()
            try:
                st.pause_gate.request()
            except Exception:
                pass
            return JSONResponse(
                {"ok": True, "message": "pausing", "session_id": st.session_id}
            )
        # Not running -> keep idempotent success
        return JSONResponse(
            {"ok": True, "message": "not running", "session_id": st.session_id}
        )

    @app.post("/api/restart")
    async def api_restart(payload: dict | None = None, request: Request = None):  # type: ignore[no-redef]
        # 当运行中：先停止再重启；当未运行：直接启动一局
        st = _get_session(_parse_sid_from(request)) if request is not None else _STATE
        if st.is_running():
            await _stop_game_for(st)
            # Await the cancelled task to let its cleanup run; suppress CancelledError
            if st.task is not None:
                try:
                    await st.task
                except asyncio.CancelledError:
                    # Expected when the background runner is cancelled while awaiting input
                    pass
                except Exception:
                    # Defensive: ignore any unexpected shutdown errors
                    pass
                finally:
                    # Drop stale reference to avoid confusion across sessions
                    st.task = None
        sid = None
        try:
            sid = str((payload or {}).get("story_id") or "").strip()
        except Exception:
            sid = None
        if sid:
            ids = _list_story_ids()
            if sid not in ids:
                return JSONResponse(
                    {"ok": False, "message": "unknown story id"}, status_code=400
                )
            st.selected_story_id = sid
        ok, msg = await _start_game_for(
            st, selected_story_id=st.selected_story_id or None
        )
        code = 200 if ok else 400
        return JSONResponse(
            {"ok": ok, "message": msg, "session_id": st.session_id}, status_code=code
        )

    @app.get("/api/state")
    async def api_state(request: Request):  # type: ignore[no-redef]
        st = _get_session(_parse_sid_from(request))
        return {
            "running": st.is_running(),
            "paused": bool(st.pause_gate.paused) if st.pause_gate else False,
            "last_sequence": st.bridge.last_sequence,
            "state": st.last_snapshot,
            "session_id": st.session_id,
        }

    @app.post("/api/player_say")
    async def api_player_say(payload: dict, request: Request):  # type: ignore[no-redef]
        """Submit a player's utterance for the current session.

        Body: {"name": "Doctor", "text": "......"}
        """
        st = _get_session(_parse_sid_from(request))
        if not st.is_running():
            return JSONResponse(
                {"ok": False, "message": "game not running"}, status_code=400
            )
        try:
            name = str(payload.get("name") or "").strip()
            text = str(payload.get("text") or "").strip()
        except Exception:
            return JSONResponse(
                {"ok": False, "message": "invalid payload"}, status_code=400
            )
        if not name or not text:
            return JSONResponse(
                {"ok": False, "message": "name/text required"}, status_code=400
            )
        try:
            await st.get_player_queue(name).put(text)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "message": f"queue error: {exc}"}, status_code=500
            )
        return JSONResponse({"ok": True})

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket):  # type: ignore[no-redef]
        st = _get_session(_parse_sid_from(ws))
        await st.bridge.register(ws)
        try:
            raw_qs = ws.scope.get("query_string", b"") or b""
            qs = parse_qs(raw_qs.decode("utf-8")) if raw_qs else {}
            since_s = (qs.get("since", ["0"]) or ["0"])[0]
            try:
                since = int(since_s or "0")
            except Exception:
                since = 0
            # hello + replay
            await ws.send_json(
                {
                    "type": "hello",
                    "last_sequence": st.bridge.last_sequence,
                    "state": st.last_snapshot,
                    "session_id": st.session_id,
                    "paused": bool(st.pause_gate.paused) if st.pause_gate else False,
                }
            )
            for ev in st.bridge.replay_since(since):
                try:
                    await ws.send_json({"type": "event", "event": ev})
                except Exception:
                    break
            # keep-alive; actual events are pushed by bridge
            while True:
                await asyncio.sleep(60)
        except WebSocketDisconnect:  # type: ignore[misc]
            pass
        finally:
            try:
                await st.bridge.unregister(ws)
            except Exception:
                pass

    # Static hosting (same-origin front-end). web_dir must exist with index.html
    if web_dir is not None and StaticFiles is not None and web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


def _run_server(
    host: str,
    port: int,
    web_dir: Optional[str],
    *,
    allow_cors_from: Optional[list[str]] = None,
) -> None:
    wd = Path(web_dir) if web_dir else (project_root() / "web")
    app = _make_app(wd, allow_cors_from=allow_cors_from)
    uvicorn.run(app, host=host, port=port, reload=False, log_level="info")


def main_once() -> None:
    # Keep original single-run behaviour for explicit --once
    main()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NPC Talk Demo server/CLI")
    p.add_argument(
        "--once", action="store_true", help="Run one game in CLI mode and exit"
    )
    p.add_argument(
        "--host", default="127.0.0.1", help="Server host (default 127.0.0.1)"
    )
    p.add_argument("--port", type=int, default=8000, help="Server port (default 8000)")
    p.add_argument(
        "--web-dir",
        default=str(project_root() / "web"),
        help="Directory to serve as frontend (default ./web)",
    )
    p.add_argument(
        "--cors",
        default="",
        help="Comma separated origins to allow CORS (empty means disabled)",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    if args.once:
        main_once()
    else:
        if FastAPI is None or uvicorn is None:
            print(
                "FastAPI/uvicorn is required for server mode. Install with: pip install fastapi 'uvicorn[standard]'"
            )
            sys.exit(2)
        allow_origins = [o.strip() for o in args.cors.split(",") if o.strip()] or None
        _run_server(args.host, args.port, args.web_dir, allow_cors_from=allow_origins)

# Explicit public API for importers/tests
__all__ = [
    "Event",
    "EventBus",
    "EventType",
    "StructuredLogger",
    "StoryLogger",
    "make_kimi_npc",
    "make_npc_actions",
    "run_demo",
    "create_logging_context",
]
