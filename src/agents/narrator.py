"""
Narrator: generates a single micro-narration sentence (Chinese, no labels) after
an action is adjudicated. Uses an LLM (OpenAI-compatible via Agentscope) with
policies to avoid repetition and keep immersion.
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional

from agentscope.model import OpenAIChatModel  # type: ignore


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
        sys = (
            "你是一位镜头语句作者，为桌面场景即时写一段中文白描，重点呈现刚刚动作的余波/后果对环境造成的影响。\n"
            "严格要求：\n"
            f"- 输出一段成文，不少于1句、不多于{max_sent}句，总字数不超过{max_len}字。\n"
            "- 不要任何标签/引号/方括号/英文，不要解释规则。\n"
            "- 禁止出现人物姓名、人称代词（我/你/他/她/我们/你们/他们等）、职业称谓和行为指令；不要直接描写人物的动作或处置。\n"
            "- 只描写环境与感官（视觉/声响/空气/气味/光影/物件/阴影/震动），聚焦一到两个意象，体现‘刚刚动作的余波/痕迹/震动/位移/声响/气味变化’。\n"
            "- 不要返回列表/数组/代码块/JSON；只输出自然段文本。\n"
            "- 避免与最近叙述重复，尽量避开以下高频短语：{avoid_list}.\n"
        ).format(avoid_list=", ".join(avoid) or "无")

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

        usr = (
            f"地点：{loc}；紧张度：{band}；焦点：{focus}\n"
            f"环境关键词（可选用，但不要原样罗列）：{bins_text}\n"
            f"后效线索（仅作灵感，不要原样罗列）：{cues_text}\n"
            f"环境刻痕：{', '.join(marks) if marks else '无'}\n"
            f"请直接输出一段中文白描（1-{max_sent}句），不要任何标签/JSON/额外说明。"
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
        if self._debug_log:
            try:
                self._debug_log(f"NARR final: {final}")
            except Exception:
                pass
        self.add_history(final)
        return final
