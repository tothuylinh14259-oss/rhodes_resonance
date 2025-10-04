"""
Narrator: generates a single micro-narration sentence (Chinese, no labels) after
an action is adjudicated. Uses an LLM (OpenAI-compatible via Agentscope) with
policies to avoid repetition and keep immersion.
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional

from agentscope.model import OpenAIChatModel  # type: ignore


PROMPT_OPENING_REQUIRE_ACTION = "你是一位镜头语句作者，为桌面场景即时写一段中文白描，呈现刚才的动作结果及其对环境造成的余波/后果。\n"
PROMPT_OPENING_AFTEREFFECT = "你是一位镜头语句作者，为桌面场景即时写一段中文白描，重点呈现刚刚动作的余波/后果对环境造成的影响。\n"

PROMPT_DESC_WITH_ACTION = (
    "- 描写动作结果与其对环境/感官的影响，可结合视觉/声响/空气/气味/光影/物件/阴影/震动；避免内心独白与解释。\n"
)
PROMPT_DESC_ENV_ONLY = (
    "- 只描写环境与感官（视觉/声响/空气/气味/光影/物件/阴影/震动），聚焦一到两个意象，体现‘刚刚动作的余波/痕迹/震动/位移/声响/气味变化’。\n"
)
PROMPT_RESULT_REQUIRE_ACTION = "- 基于已裁决结果（如命中/未中/伤害/成败）进行描写，不要臆测未来进展。\n"

PROMPT_STRICT_HEADER = "严格要求：\n"
PROMPT_STRICT_LENGTH_TEMPLATE = (
    "- 输出一段成文，不少于1句、不多于{max_sent}句，总字数不超过{max_len}字。\n"
)
PROMPT_STRICT_STYLE = "- 不要任何标签/引号/方括号/英文，不要解释规则。\n"
PROMPT_STRICT_FORMAT = "- 不要返回列表/数组/代码块/JSON；只输出自然段文本。\n"

PROMPT_AVOID_TEMPLATE = "- 避免与最近叙述重复，尽量避开以下高频短语：{avoid_list}.\n"

PROMPT_ACTION_REQUIRE_FIRST = (
    "- 输出必须包含动作结果的描写，可与环境余波交织；建议先写结果后写余波。\n"
)
PROMPT_ACTION_REQUIRE_FLEX = (
    "- 输出必须包含动作结果的描写；可与环境余波交织，顺序不限。\n"
)


def _char_bigrams(s: str) -> set[str]:
    s = (s or "").strip()
    if len(s) < 2:
        return set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


class Narrator:
    def __init__(self, model_cfg: Dict[str, Any] | None, policy: Dict[str, Any] | None = None) -> None:
        """Create a narrator using model config section `narration` from configs/model.json.

        model_cfg example:
        { "base_url": "...", "narration": {"model":"...","temperature":0.9,"top_p":0.9,
                           "presence_penalty":0.3, "frequency_penalty":0.3, "stream": false } }
        policy example:
        { "max_len": 36, "candidates": 3, "history_size": 8,
          "focus_cycle": ["visual","sound","air","motion","light","object"] }
        """
        import os
        self._policy = dict(policy or {})
        root = dict(model_cfg or {})
        sec = dict(root.get("narration") or root)
        base_url = root.get("base_url") or os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
        self._model = OpenAIChatModel(
            model_name=sec.get("model") or os.getenv("KIMI_MODEL", "kimi-k2-turbo-preview"),
            api_key=os.environ["MOONSHOT_API_KEY"],
            stream=bool(sec.get("stream", False)),
            client_args={"base_url": base_url},
            generate_kwargs={
                "temperature": float(sec.get("temperature", 0.9)),
                "top_p": float(sec.get("top_p", 0.9)),
                "presence_penalty": float(sec.get("presence_penalty", 0.3)),
                "frequency_penalty": float(sec.get("frequency_penalty", 0.3)),
            },
        )
        self._env_keywords: Dict[str, Dict[str, List[str]]] = {}
        self._history: List[str] = []
        self._step: int = 0
        self._debug_log = None  # optional callable(str)

    def set_env_keywords(self, scenes: Dict[str, Dict[str, List[str]]]) -> None:
        self._env_keywords = dict(scenes or {})

    def add_history(self, line: str) -> None:
        if not line:
            return
        self._history.append(line.strip())
        hs = int(self._policy.get("history_size", 8))
        if len(self._history) > max(1, hs):
            self._history = self._history[-hs:]

    def set_debug_logger(self, fn) -> None:
        """Set a callable that accepts a string to write narrator debug lines."""
        self._debug_log = fn

    def _focus(self) -> str:
        cyc = self._policy.get("focus_cycle") or ["visual", "sound", "air", "motion", "light", "object"]
        if not isinstance(cyc, list) or not cyc:
            cyc = ["visual", "sound", "air", "motion", "light", "object"]
        f = str(cyc[self._step % len(cyc)])
        self._step += 1
        return f

    async def generate(self, meta: Dict[str, Any], snap: Dict[str, Any]) -> str:
        """Return a single Chinese sentence (<=max_len, no labels) as micro-narration.

        meta: { 'actor','target','kind','hit':bool,'damage':int,'ko':bool,'time_cost':int }
        snap: world snapshot including 'location','tension','marks' if present
        """
        # Build context
        loc = str(snap.get("location", "场景"))
        tension = int(snap.get("tension", 1))
        marks = snap.get("marks") or []
        try:
            marks = [str(m) for m in marks][:3]
        except Exception:
            marks = []
        focus = self._focus()
        # Pick scene keyword bins
        env = self._env_keywords.get(loc) or {}
        # Build avoid list from history (common 2-gram)
        avoid: List[str] = []
        grams: Dict[str, int] = {}
        for h in self._history[-int(self._policy.get("history_size", 8)) :]:
            for g in _char_bigrams(h):
                grams[g] = grams.get(g, 0) + 1
        avoid = [g for g, _ in sorted(grams.items(), key=lambda x: -x[1])[:6]]

        # Compose prompts
        max_len = int(self._policy.get("max_len", 80))
        max_sent = int(self._policy.get("max_sentences", 3))
        # Build policy-driven rule lines
        relaxed = bool(self._policy.get("relaxed_anonymity", False))
        # Fine-grained allowances
        allow_names = bool(self._policy.get("allow_names", False))
        allow_pronouns = bool(self._policy.get("allow_pronouns", False))
        allow_professions = bool(self._policy.get("allow_professions", False))
        allow_actions = bool(self._policy.get("allow_actions", False))
        # Determine which elements remain restricted
        disallow_segments: list[str] = []
        if not allow_names:
            disallow_segments.append("人物姓名")
        if not allow_pronouns:
            disallow_segments.append("人称代词（我/你/他/她/我们/你们/他们等）")
        if not allow_professions:
            disallow_segments.append("职业称谓")
        # Build anonymity/action rule line
        rule_lines: list[str] = []
        if disallow_segments:
            head = "尽量避免出现" if relaxed else "禁止出现"
            rule_lines.append(f"- {head}" + "、".join(disallow_segments) + "。\n")
        if not allow_actions:
            tail = "尽量不要" if relaxed else "不要"
            rule_lines.append(f"- {tail}直接描写人物的动作或处置。\n")
        rule_anonymity = "".join(rule_lines)
        include_avoid = not bool(self._policy.get("disable_avoid_list", False))
        avoid_line = (
            PROMPT_AVOID_TEMPLATE.format(avoid_list=", ".join(avoid) or "无")
            if include_avoid
            else ""
        )
        # Action requirement rules (elastic order)
        require_action = bool(self._policy.get("require_action", False))
        action_first = bool(self._policy.get("action_first", False))
        rule_action = (
            PROMPT_ACTION_REQUIRE_FIRST if action_first else PROMPT_ACTION_REQUIRE_FLEX
        ) if require_action else ""

        # Opening line: if require_action, explicitly mention "动作+余波"
        opening = (
            PROMPT_OPENING_REQUIRE_ACTION if require_action else PROMPT_OPENING_AFTEREFFECT
        )
        desc_line = (
            PROMPT_DESC_WITH_ACTION if (require_action or allow_actions) else PROMPT_DESC_ENV_ONLY
        )
        result_line = PROMPT_RESULT_REQUIRE_ACTION if require_action else ""
        sys_parts = [
            opening,
            PROMPT_STRICT_HEADER,
            PROMPT_STRICT_LENGTH_TEMPLATE.format(max_sent=max_sent, max_len=max_len),
            PROMPT_STRICT_STYLE,
            rule_anonymity,
            desc_line,
            result_line,
            PROMPT_STRICT_FORMAT,
            avoid_line,
            rule_action,
        ]
        sys = "".join(sys_parts)

        # Provide context compactly; do not leak numbers but indicate intensity bands
        # Intensity cues only (no actor/target or explicit outcomes)
        band = "平和" if tension <= 1 else ("收紧" if tension <= 3 else "压抑")

        # Scene bins
        bins = []
        for k in ("visual", "sound", "air", "props"):
            vals = env.get(k) or []
            if isinstance(vals, list) and vals:
                bins.append(f"{k}:{'|'.join(vals[:6])}")
        bins_text = "; ".join(bins) if bins else ""

        # Build user content and ask for N candidates as JSON array
        # Build aftereffect cues from meta
        cues: list[str] = []
        kind = str(meta.get("kind") or "")
        hit = bool(meta.get("hit") or False)
        dmg = int(meta.get("damage") or 0)
        ko = bool(meta.get("ko") or False)
        success = bool(meta.get("success") or False)
        if kind == "attack":
            if hit:
                if dmg >= 6:
                    cues += ["重响贴地", "桌面微移", "椅脚划地", "空气骤紧"]
                else:
                    cues += ["闷响", "椅脚轻震", "金属件微颤", "发梢轻颤"]
            else:
                cues += ["拳风掠过", "纸页轻响", "灯影一滞", "冷气微涌"]
            if ko:
                cues += ["椅脚划地声", "影子一折"]
        elif kind == "talk":
            cues += ["气压回落", "通风口低鸣显露", "纸页沉静", "灯影平整"]
        elif kind == "skill_check":
            if success:
                cues += ["翻页定在标注处", "投影反光停在图表", "笔记纸边微翘"]
            else:
                cues += ["翻页声停顿", "空白页反光", "指节轻叩声独立"]
        else:
            cues += ["步声短促", "椅脚轻响", "徽章轻晃", "空气微颤"]

        cues_text = "、".join(cues[:6])

        # Build a concise action summary for guidance (not to be copied verbatim)
        def _short(s: str) -> str:
            try:
                return s.strip()
            except Exception:
                return s
        actor = _short(str(meta.get("actor") or ""))
        target = _short(str(meta.get("target") or ""))
        kind = _short(str(meta.get("kind") or ""))
        action_bits: list[str] = []
        if kind == "attack":
            base = (f"{actor}对{target}发动攻击" if actor and target else (f"{actor}发动攻击" if actor else (f"对{target}发动攻击" if target else "发动攻击")))
            hit = bool(meta.get("hit") or False)
            dmg = int(meta.get("damage") or 0)
            ko = bool(meta.get("ko") or False)
            tail = "命中" if hit else "未中"
            extra = (f"，造成{dmg}伤害" if hit and dmg > 0 else "") + ("，目标倒地" if ko else "")
            action_bits.append(base + "，" + tail + extra)
        elif kind == "talk":
            if actor and target:
                action_bits.append(f"{actor}与{target}交谈")
            else:
                action_bits.append("交谈")
        elif kind == "skill_check":
            skill = _short(str(meta.get("skill") or "检定"))
            succ = bool(meta.get("success") or False)
            if actor:
                action_bits.append(f"{actor}进行{skill}{'，成功' if succ else '，失败'}")
            else:
                action_bits.append(f"进行{skill}{'，成功' if succ else '，失败'}")
        elif kind == "move":
            if actor:
                action_bits.append(f"{actor}移动")
            else:
                action_bits.append("移动")
        elif kind == "assist":
            if actor and target:
                action_bits.append(f"{actor}协助{target}")
            else:
                action_bits.append("协助")
        elif kind == "wait":
            if actor:
                action_bits.append(f"{actor}待命")
            else:
                action_bits.append("待命")
        else:
            if actor:
                action_bits.append(f"{actor}行动")
            else:
                action_bits.append("执行动作")
        action_text = "；".join([b for b in action_bits if b])

        # Optionally omit the 'focus' cue to reduce steering
        omit_focus = bool(self._policy.get("omit_focus", False))
        header = (
            (f"地点：{loc}；紧张度：{band}\n" if omit_focus else f"地点：{loc}；紧张度：{band}；焦点：{focus}\n")
        )
        usr = (
            header
            + (f"环境关键词（可选用，但不要原样罗列）：{bins_text}\n" if bins_text else "")
            + (f"后效线索（仅作灵感，不要原样罗列）：{cues_text}\n" if cues_text else "")
            + (f"结果要点（仅作灵感，不要原样罗列）：{action_text}\n" if action_text and require_action else "")
            + f"环境刻痕：{', '.join(marks) if marks else '无'}\n"
            + f"请直接输出一段中文白描（1-{max_sent}句），不要任何标签/JSON/额外说明。"
        )

        try:
            if self._debug_log:
                try:
                    self._debug_log("NARR prompt(sys): " + sys.replace("\n", " "))
                    self._debug_log("NARR prompt(usr): " + usr.replace("\n", " "))
                except Exception:
                    pass
            res = await self._model([
                {"role": "system", "content": sys},
                {"role": "user", "content": usr},
            ])
            if self._debug_log:
                try:
                    self._debug_log(f"NARR res_type: {type(res)!r}")
                    # Try to reveal common attributes briefly
                    for name in ("content", "choices", "message"):
                        try:
                            val = getattr(res, name)
                        except Exception:
                            val = None
                        if val is not None:
                            preview = str(val)
                            if len(preview) > 300:
                                preview = preview[:300] + "…"
                            self._debug_log(f"NARR attr {name}: type={type(val)!r} preview={preview}")
                except Exception:
                    pass

            # Extract text (robust against SDKs that raise in __getattr__)
            text: Optional[str] = None
            try:
                get_text = getattr(res, "get_text_content", None)
            except Exception:
                get_text = None
            try:
                if callable(get_text):
                    text = get_text()
            except Exception:
                text = None
            if text is None:
                c = getattr(res, "content", None)
                if isinstance(c, str):
                    text = c
                elif isinstance(c, list):
                    parts: List[str] = []
                    for blk in c:
                        if isinstance(blk, dict) and blk.get("type") == "text":
                            parts.append(blk.get("text", ""))
                    text = "".join(parts) if parts else None
            if self._debug_log:
                try:
                    self._debug_log(f"NARR raw: {text if text is not None else '<None>'}")
                except Exception:
                    pass
        except Exception as e:
            text = None
            if self._debug_log:
                try:
                    self._debug_log(f"NARR error: {e!r}")
                except Exception:
                    pass
        # Plain text narration: strip and enforce max length and sentence cap
        final = (text or "").strip()
        # Strip simple code fences if present
        if final.startswith("```"):
            try:
                first_nl = final.find("\n")
                if first_nl != -1:
                    final = final[first_nl + 1 :]
                if final.endswith("```"):
                    final = final[:-3]
            except Exception:
                pass
            final = final.strip()
        # Enforce sentence/length policy (soft cut)
        try:
            sentences = [s for s in final.replace("！","。").replace("?","。").split("。") if s.strip()]
            if len(sentences) > max_sent:
                final = "。".join(sentences[:max_sent])
        except Exception:
            pass
        if len(final) > max_len:
            final = final[:max_len]
        # Optionally enforce explicit result mention (post-generation guardrail)
        try:
            enforce_result = bool(self._policy.get("enforce_result", False)) or require_action
        except Exception:
            enforce_result = require_action
        if enforce_result:
            kind_l = (kind or "").lower() if isinstance(kind, str) else str(kind)
            tokens = ["命中", "未中", "成功", "失败", "伤害", "倒地", "击倒", "落空", "中招", "受创"]
            need_patch = not any(tok in final for tok in tokens)
            if need_patch:
                # Build a short, natural prefix
                prefix = ""
                try:
                    if kind_l == "attack":
                        hit = bool(meta.get("hit") or False)
                        dmg = int(meta.get("damage") or 0)
                        ko = bool(meta.get("ko") or False)
                        if hit:
                            if ko:
                                prefix = "一击命中，目标倒地"
                            elif dmg >= 6:
                                prefix = f"重击命中，造成{dmg}点伤害"
                            elif dmg > 0:
                                prefix = f"命中，造成{dmg}点伤害"
                            else:
                                prefix = "命中"
                        else:
                            prefix = "攻势落空"
                    elif kind_l == "skill_check":
                        succ = bool(meta.get("success") or False)
                        prefix = "检定成功" if succ else "检定失败"
                    elif kind_l == "investigate":
                        succ = bool(meta.get("success") or False)
                        prefix = "搜查有获" if succ else "搜查无果"
                except Exception:
                    prefix = ""
                if prefix:
                    if final:
                        final = prefix + "，" + final
                    else:
                        final = prefix
                    if len(final) > max_len:
                        final = final[:max_len]
        if self._debug_log:
            try:
                self._debug_log(f"NARR final: {final}")
            except Exception:
                pass
        self.add_history(final)
        return final
