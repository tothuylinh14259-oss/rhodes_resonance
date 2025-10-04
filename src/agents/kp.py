"""
KPAgent: Game Master that rewrites the Player's input to fit world/persona
without negating the player's intent. The goal is to preserve the core intent
and turn it into in-world dialogue or a small, executable action.
Behavior:
- Never negate or reject the player. No "否定/拒绝"输出。
- Default path is ACCEPT: output a sanitized Player message
  (Msg(name='Player', role='user')).
- Only when表达含糊不清难以保留意愿时，CLARIFY：向玩家提出一个简短澄清问题。
This agent uses Kimi (OpenAI-compatible) via Agentscope OpenAIChatModel.
"""
from __future__ import annotations
import json
from typing import List, Optional, Callable
from agentscope.agent import AgentBase  # type: ignore
from agentscope.message import Msg  # type: ignore
from agentscope.model import OpenAIChatModel  # type: ignore
_SYSTEM_PROMPT = (
    "你是KP（守秘人/主持人），负责将玩家在当前游戏世界中的发言/行动，"
    "在不改变玩家核心意愿的前提下，改写为符合世界观与角色人设的对白或可执行的小动作。\n"
    "世界规则（示例）：\n"
    "- 无现代科技（手机/枪支/无人机/电器等），没有瞬间传送。\n"
    "- 常识一致（角色只知道自己经历；不越权知晓他人隐私或未来）。\n"
    "- 行动应具体、可执行、短小；避免一回合内完成过多复杂行动。\n"
    "- 角色个性/动机应合理；避免违背已知设定。\n"
    "改写原则：\n"
    "- 绝不否定玩家意愿；若表达夸张/越界，用更贴合世界观的方式表达同等意图（如‘我是神’→‘我自称神谕者/受神启示之人’）。\n"
    "- 说人话：对白不超过1-2句；若含行动，写明具体动作（如‘举杯敬酒并自我介绍’）。\n"
    "- 必要时才澄清，问题要短且具体，只问一个点。\n"
    "- 当玩家选择跳过（/skip 或‘(玩家选择跳过本回合)’）时，将其意图改写为一条‘被动姿态/微动作/轻观察’的一句话，不触发工具与推进剧情，只表达他此刻的在场与状态。\n"
    "输出严格JSON（不要markdown围栏/额外文字）。当意图需要明确对象时（如 attack/talk/assist/investigate/use_item），必须给出 target；若无法确定目标，请返回 clarify 并提出一个只问目标对象的简短问题：\n"
    "{\n"
    "  \"decision\": \"accept|clarify\",\n"
    "  \"sanitized\": \"当decision=accept时，给出改写后的玩家对白/行动（1-2句）。\",\n"
    "  \"intent\": { \"intent\": \"attack|talk|investigate|move|assist|use_item|skill_check|wait\", \n              \"target\": \"目标名称或对象（必须给出；无法确定则不要accept而是clarify）\", \n              \"skill\": \"perception|medicine|...\", \n              \"ability\": \"STR|DEX|CON|INT|WIS|CHA\", \n              \"proficient\": true, \n              \"dc_hint\": 12, \n              \"damage_expr\": \"1d4+STR\", \n              \"time_cost\": 1, \n              \"notes\": \"一句话说明意图\" },\n"
    "  \"question\": \"当decision=clarify时，提出一个具体且简短的问题（若缺目标，只问目标）。\"\n"
    "}"
)
class KPAgent(AgentBase):
    def __init__(self, name: str = "KP", player_persona: str | None = None, player_name: str = "Player") -> None:
        super().__init__()
        self.name = name
        self.transcript: List[Msg] = []
        self._last_processed_player_id: Optional[str] = None
        self._awaiting_player: bool = False
        self._awaiting_confirm: bool = False
        self._pending_sanitized: Optional[str] = None
        self._pending_intent: Optional[dict] = None
        self.player_name = player_name
        # Compose system prompt with optional player persona
        self._sys_prompt = _SYSTEM_PROMPT
        if player_persona:
            self._sys_prompt += "\n玩家人设（用于改写口吻与动机）：\n" + player_persona.strip() + "\n"
        # Optional world snapshot provider; set via setter to avoid hard coupling
        self._world_snapshot_provider: Optional[Callable[[], dict]] = None
        # Initialize Kimi (OpenAI-compatible) model client (non-streaming)
        import os
        self.model = OpenAIChatModel(
            model_name=os.getenv("KIMI_MODEL", "kimi-k2-turbo-preview"),
            api_key=os.environ["MOONSHOT_API_KEY"],
            stream=False,
            client_args={"base_url": os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")},
            generate_kwargs={"temperature": 0.2},
        )
        # Optional director policy for dynamic encounter/foe insertion
        self._director_policy: str = (
            "导演策略：在不突兀的前提下，依据场景目标、时间压力与上下文冲突，酌情引入外部压力（如巡逻、敌对势力、事故），"
            "以推动剧情向既定目标推进或制造合理阻力。避免过于频繁的插入；每次插入须给出清晰理由。"
        )
        # Optional name map for canonicalization (e.g., 中文→内部ID)
        self._name_map: dict[str, str] = {"阿米娅": "Amiya", "凯尔希": "Kaltsit", "博士": "Doctor"}
        # Optional story script (acts/beats) and runtime state
        self._story: Optional[dict] = None
        self._fired_beats: set[str] = set()
        self._beat_last_min: dict[str, int] = {}
        self._stalled_rounds: int = 0
        self._last_obj_status_sig: str = ""
        # Optional external rules
        self._time_rules: Optional[dict] = None  # { intent_cost_min: {intent:int}, default_min:int }
        self._relation_rules: Optional[dict] = None  # { talk_default_delta:int, multi_target_split:bool }
        # Optional narrator and feature flags
        self._narrator = None  # set via set_narrator()
        self._suppress_mech_narration: bool = True
        self._strict_spawn: bool = False  # 禁用LLM回退spawn，仅允许剧情脚本驱动
        # Player text handling
        self._auto_accept: bool = False      # 是否直接接受，无需 /yes（默认否）
        self._loose_target: bool = False     # 缺少 target 时是否不阻塞澄清（默认否）
        self._preserve_text: bool = True     # 是否尽量保留玩家原文，不做语义改写（默认是）
    async def observe(self, msg: Msg | List[Msg] | None) -> None:
        if msg is None:
            return
        if isinstance(msg, list):
            self.transcript.extend(msg)
        else:
            self.transcript.append(msg)
    async def reply(self, msg: Msg | List[Msg] | None = None) -> Msg:
        player_msg = self._get_latest_player_msg()
        if not player_msg:
            # No new player input to check; keep silent politely
            out = Msg(name=self.name, content="（KP点头，暂时无事）", role="assistant")
            await self.print(out)
            return out
        # If waiting for confirmation and got player's response now
        if self._awaiting_confirm and player_msg.id != self._last_processed_player_id:
            raw = (player_msg.get_text_content() or "")
            # Flexible confirmation: accept '是/对/好的/确认/yes/y' etc.
            norm = self._normalize_confirm_text(raw)
            # Accept confirmation when player explicitly says yes or sends an empty line
            if self._is_yes(norm) or raw.strip() == "/yes" or raw.strip() == "":
                content = (self._pending_sanitized or "").strip()
                try:
                    import json as _json
                    if isinstance(self._pending_intent, dict):
                        intent_json = _json.dumps(self._pending_intent, ensure_ascii=False)
                        if intent_json and intent_json != "{}":
                            content += "\n```json\n" + intent_json + "\n```"
                except Exception:
                    pass
                 # ensure required target exists
                req = {"attack","talk","assist","investigate","use_item"}
                try:
                    k = str((self._pending_intent or {}).get("intent") or "").lower()
                    tgt = (self._pending_intent or {}).get("target")
                    if k in req and not tgt:
                        # try infer from content or context
                        t = self._infer_target_from_text(content) or self._infer_target_from_text(self._build_context_text(6))
                        if t:
                            self._pending_intent = dict(self._pending_intent or {}); self._pending_intent["target"] = t
                            # rebuild content with updated intent json
                            import json as _json
                            ij = _json.dumps(self._pending_intent, ensure_ascii=False)
                            content = (self._pending_sanitized or "").strip() + "\n```json\n" + ij + "\n```"
                        else:
                            ask = Msg(name=self.name, content="本次行动需要明确目标对象，请指明目标名称。", role="assistant")
                            self._awaiting_player = True; self._awaiting_confirm = False
                            await self.print(ask); return ask
                except Exception:
                    pass
                final_msg = Msg(name=self.player_name, content=content, role="user")
                await self.print(final_msg)
                self._awaiting_confirm = False
                self._awaiting_player = False
                self._pending_sanitized = None
                self._pending_intent = None
                self._last_processed_player_id = player_msg.id
                return final_msg
            # Treat as incremental slot filling or new intent
            # Quick slot fill: try to update target from this short reply
            try:
                tquick = self._infer_target_from_text(raw)
                if tquick and isinstance(self._pending_intent, dict):
                    if not self._pending_intent.get("target"):
                        self._pending_intent["target"] = tquick
            except Exception:
                pass
            judged2 = await self._judge_player_input(player_msg)
            sanitized2 = judged2.get("sanitized") or self._fallback_sanitize(raw)
            self._pending_sanitized = sanitized2
            # Also refresh pending intent (so confirmation embeds a valid intent)
            intent2 = judged2.get("intent") if isinstance(judged2.get("intent"), dict) else None
            req = {"attack","talk","assist","investigate","use_item"}
            kind2 = str((intent2 or {}).get("intent") or "").lower()
            if kind2 in req and not ((intent2 or {}).get("target")):
                prev_t = (self._pending_intent or {}).get("target")
                if prev_t:
                    intent2 = intent2 or {}; intent2["target"] = prev_t
                else:
                    inferred = self._infer_target_from_text(raw) or self._infer_target_from_text(self._build_context_text(6))
                    if inferred:
                        intent2 = intent2 or {}; intent2["target"] = inferred
                    else:
                        ask = Msg(name=self.name, content="本次行动需要明确目标对象，请指明目标名称。", role="assistant")
                        self._awaiting_player = True; self._awaiting_confirm = False
                        await self.print(ask); return ask
            if intent2 is None:
                intent2 = self._extract_intent_from_text(raw) or {"intent": "wait", "notes": "未能解析，默认等待"}
            # Merge into pending intent (slot memory)
            if isinstance(self._pending_intent, dict):
                base = dict(self._pending_intent)
                for k, v in (intent2 or {}).items():
                    if v:
                        base[k] = v
                self._pending_intent = base
            else:
                self._pending_intent = intent2
            self._awaiting_confirm = True
            self._awaiting_player = True
            confirm_new = Msg(name=self.name, content=f"我理解为：{sanitized2}。若正确请回复 /yes 确认。", role="assistant")
            await self.print(confirm_new)
            return confirm_new
        # If this player message is already processed, acknowledge
        if player_msg.id == self._last_processed_player_id:
            out = Msg(name=self.name, content="（已确认上一条输入）", role="assistant")
            await self.print(out)
            return out
        # Call Kimi to rewrite/judge the latest player input
        judged = await self._judge_player_input(player_msg)
        decision = judged.get("decision")
        if decision == "accept":
            raw_text = player_msg.get_text_content() or ""
            sanitized = judged.get("sanitized") or raw_text
            if getattr(self, "_preserve_text", True):
                sanitized = raw_text
            intent_obj = judged.get("intent") if isinstance(judged.get("intent"), dict) else None
            # 自动接受模式：直接输出玩家最终消息，不再二次确认
            if getattr(self, "_auto_accept", False):
                content = sanitized
                try:
                    import json as _json
                    if isinstance(intent_obj, dict):
                        ij = _json.dumps(intent_obj, ensure_ascii=False)
                        if ij and ij != "{}":
                            content += "\n```json\n" + ij + "\n```"
                except Exception:
                    pass
                final_msg = Msg(name=self.player_name, content=content, role="user")
                await self.print(final_msg)
                self._last_processed_player_id = player_msg.id
                self._awaiting_confirm = False
                self._awaiting_player = False
                self._pending_sanitized = None
                self._pending_intent = None
                return final_msg
            # 默认流程：仍需 /yes 确认
            self._awaiting_player = True
            self._awaiting_confirm = True
            self._pending_sanitized = sanitized
            self._pending_intent = intent_obj
            confirm = Msg(name=self.name, content=f"我理解为：{sanitized}。若正确请回复 /yes 确认。", role="assistant")
            await self.print(confirm)
            return confirm
        if decision == "clarify":
            # Cache/merge partial intent for memory
            maybe_intent = judged.get("intent") if isinstance(judged.get("intent"), dict) else None
            if maybe_intent:
                if isinstance(self._pending_intent, dict):
                    base = dict(self._pending_intent)
                    for k, v in maybe_intent.items():
                        if v:
                            base[k] = v
                    self._pending_intent = base
                else:
                    self._pending_intent = dict(maybe_intent)
            # If only target missing, try infer from this message and confirm directly
            try:
                need_target = False
                kind = str(((self._pending_intent or {}).get("intent")) or "").lower()
                if kind in {"attack","talk","assist","investigate","use_item"}:
                    need_target = not bool((self._pending_intent or {}).get("target"))
                if need_target:
                    inferred = self._infer_target_from_text(player_msg.get_text_content() or "")
                    if inferred:
                        self._pending_intent = dict(self._pending_intent or {})
                        self._pending_intent["target"] = inferred
                        sanitized = judged.get("sanitized") or (player_msg.get_text_content() or "")
                        self._pending_sanitized = sanitized
                        self._awaiting_player = True
                        self._awaiting_confirm = True
                        confirm = Msg(name=self.name, content=f"我理解为：{sanitized}。若正确请回复 /yes 确认。", role="assistant")
                        await self.print(confirm)
                        return confirm
            except Exception:
                pass
            # Otherwise ask the clarify question
            q = judged.get("question") or "请更具体说明你的行动。"
            self._awaiting_player = True
            self._awaiting_confirm = False
            ask = Msg(name=self.name, content=q, role="assistant")
            await self.print(ask)
            return ask
        # Fallback：当判定异常时，尽量保意愿接受，并提示一次澄清
        self._awaiting_player = True
        self._awaiting_confirm = True
        self._pending_sanitized = player_msg.get_text_content() or ""
        self._pending_intent = {"intent": "wait", "notes": "未能解析，默认等待"}
        confirm2 = Msg(name=self.name, content=f"我理解为：{self._pending_sanitized}。若正确请回复 /yes 确认。", role="assistant")
        await self.print(confirm2)
        return confirm2
    # Expose a helper for the host to know if more Player input is required
    def wants_player_reply(self) -> bool:
        return bool(self._awaiting_player)
    async def handle_interrupt(self, *args, **kwargs) -> Msg:
        msg = Msg(name=self.name, content="（KP中断）", role="assistant")
        await self.print(msg)
        return msg
    def _get_latest_player_msg(self) -> Optional[Msg]:
        for m in reversed(self.transcript):
            if m.name == self.player_name and m.role == "user":
                return m
        return None
    async def _judge_player_input(self, player_msg: Msg) -> dict:
        # Build chat messages for the OpenAI-compatible API with context
        content = player_msg.get_text_content() or ""
        ctx_text = self._build_context_text(max_items=16)
        world_text = self._format_world_snapshot()
        messages = [
            {"role": "system", "content": self._sys_prompt},
            {"role": "user", "content": f"最近上下文（供参考，勿重复）：\n{ctx_text}"},
        ]
        if world_text:
            messages.append({"role": "user", "content": f"世界状态（摘要）：\n{world_text}"})
        messages.append({"role": "user", "content": f"玩家输入：{content}"})
        res = await self.model(messages)
        # Robustly extract text from ChatResponse without relying on hasattr()
        text = None
        try:
            get_text = getattr(res, "get_text_content", None)
            if callable(get_text):
                text = get_text()
        except Exception:
            # Some objects may raise non-AttributeError in __getattr__; ignore
            text = None
        if text is None:
            # Fallbacks: try common shapes
            content = getattr(res, "content", None)
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                # Gather text blocks
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                text = "".join(parts) if parts else None
        if not text:
            return {"decision": "clarify", "question": "请简要说明你的行动目标与方式。"}
        # Try parse JSON
        try:
            judged = json.loads(self._strip_code_fences(text))
            judged = self._ensure_target_or_clarify(judged, content, prev_target=(self._pending_intent or {}).get("target"), ctx_text=self._build_context_text(6))
            if not isinstance(judged, dict):
                return {"decision": "clarify", "question": "请明确你的目标对象与动作。"}
            return judged
        except Exception:
            return {"decision": "clarify", "question": "请用更具体、可执行的描述表达你的行动。"}
    @staticmethod
    def _strip_code_fences(text: str) -> str:
        s = text.strip()
        if s.startswith("```"):
            # remove first fence line
            if "\n" in s:
                first, rest = s.split("\n", 1)
                s = rest
            s = s.strip()
            if s.endswith("```"):
                s = s[:-3]
        return s.strip()
    @staticmethod
    def _normalize_confirm_text(text: str) -> str:
        s = text.strip().lower()
        # remove common punctuation/spaces
        drop = set(" 。！!？?，,；;：:~～·… \t\r\n")
        s = "".join(ch for ch in s if ch not in drop)
        return s
    @staticmethod
    def _is_yes(norm: str) -> bool:
        yes_set = {"是", "是的", "好", "好的", "确认", "yes", "y", "行", "嗯", "对", "对的"}
        return norm in yes_set
    @staticmethod
    def _is_no(norm: str) -> bool:
        no_set = {"否", "不是", "不", "取消", "no", "n", "算了", "不要"}
        return norm in no_set
    def set_world_snapshot_provider(self, provider: Callable[[], dict]) -> None:
        """Inject a callable that returns world snapshot dict."""
        self._world_snapshot_provider = provider
    def _format_world_snapshot(self) -> str:
        if not self._world_snapshot_provider:
            return ""
        try:
            s = self._world_snapshot_provider() or {}
        except Exception:
            return ""
        # Format known fields if present
        time_min = s.get("time_min")
        if isinstance(time_min, int):
            hh = time_min // 60
            mm = time_min % 60
            time_str = f"{hh:02d}:{mm:02d}"
        else:
            time_str = "未知"
        weather = s.get("weather", "未知")
        rels = s.get("relations", {})
        try:
            rel_lines = [f"{k}:{v}" for k, v in rels.items()]
        except Exception:
            rel_lines = []
        inv = s.get("inventory", {})
        inv_lines = []
        try:
            for who, bag in inv.items():
                bag_line = ", ".join(f"{it}:{cnt}" for it, cnt in (bag or {}).items())
                inv_lines.append(f"{who}[{bag_line}]")
        except Exception:
            pass
        lines = [
            f"时间：{time_str}",
            f"天气：{weather}",
            ("关系：" + "; ".join(rel_lines)) if rel_lines else "关系：无变动",
            ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
        ]
        return "\n".join(lines)
    def _build_context_text(self, max_items: int = 16) -> str:
        # Use recent transcript (last N), show as `Name: text`
        items: List[str] = []
        for m in reversed(self.transcript[-max_items:]):
            try:
                txt = m.get_text_content()
            except Exception:
                txt = None
            if not txt:
                continue
            name = getattr(m, "name", "?")
            items.append(f"{name}: {txt}")
        if not items:
            return "(无)"
        return "\n".join(items)
    async def rewrite_skip_immediately(self) -> Msg:
        """Rewrite a player's skip into an in-world passive stance and return
        a finalized Player message without confirmation.
        This uses the same judging pipeline but with a synthetic input.
        """
        synth = Msg(name="Player", content="(玩家选择跳过本回合)", role="user")
        judged = await self._judge_player_input(synth)
        sanitized = judged.get("sanitized") or "他轻靠壁柱，指背敲了敲杯沿，示意众人继续。"
        import json as _json
        intent_json = _json.dumps(judged.get("intent") or {"intent": "wait", "notes": "skip回合"}, ensure_ascii=False)
        content = sanitized + "\n```json\n" + intent_json + "\n```"
        final_msg = Msg(name=self.player_name, content=content, role="user")
        await self.print(final_msg)
        return final_msg
    # ------------------- Adjudication API -------------------
    async def adjudicate(self, msgs: List[Msg]) -> List[Msg]:
        """Parse intents from msgs and execute world tools. Only KP mutates world.
        Returns a list of KP/Host messages summarizing results.
        """
        intents: List[tuple[str, dict]] = []
        for m in msgs:
            # Robustly extract textual content: prefer get_text_content();
            # if empty, try flattening block-style contents.
            content = ""
            try:
                content = m.get_text_content() or ""
            except Exception:
                content = ""
            if not content:
                try:
                    raw = getattr(m, "content", None)
                    if isinstance(raw, list):
                        parts: List[str] = []
                        for blk in raw:
                            try:
                                if isinstance(blk, dict) and blk.get("type") == "text":
                                    t = blk.get("text")
                                    if isinstance(t, str):
                                        parts.append(t)
                            except Exception:
                                pass
                        content = "\n".join(parts)
                    elif isinstance(raw, str):
                        content = raw
                except Exception:
                    pass
            it = self._extract_intent_from_text(content)
            if it:
                intents.append((m.name, it))
        out_msgs: List[Msg] = []
        for actor, it in intents:
            part = await self._adjudicate_one(actor, it)
            out_msgs.extend(part)
        return out_msgs
    def _extract_intent_from_text(self, text: str) -> Optional[dict]:
        s = text or ""
        if "```" in s:
            parts = s.split("```")
            for i in range(1, len(parts), 2):
                body = parts[i]
                if body.strip().startswith("json"):
                    body = body.strip()[4:]
                try:
                    obj = json.loads(body.strip())
                    if isinstance(obj, dict) and obj.get("intent"):
                        return obj
                except Exception:
                    pass
        # Fallback: try to find the last JSON object in free text (balance braces)
        obj = self._find_last_json_object(s)
        if isinstance(obj, dict) and obj.get("intent"):
            return obj
        low = s.lower()
        target = None
        # Name hint mapping: combine built-ins and custom map (case-insensitive)
        hint_map = {"amiya": "Amiya", "阿米娅": "Amiya", "kaltsit": "Kaltsit", "凯尔希": "Kaltsit", "doctor": "Doctor", "博士": "Doctor"}
        try:
            for k, v in (self._name_map or {}).items():
                hint_map[str(k).lower()] = str(v)
        except Exception:
            pass
        for k, v in hint_map.items():
            if k in s:
                target = v
                break
        if any(k in s for k in ["拳", "打", "揍", "砸", "击", "punch", "hit", "attack"]):
            return {"intent": "attack", "target": target or "", "ability": "STR", "damage_expr": "1d4+STR"}
        if any(k in s for k in ["调查", "查看", "侦查", "搜查", "perception", "investigate", "check"]):
            return {"intent": "skill_check", "skill": "perception", "dc_hint": 12}
        if any(k in s for k in ["说", "问", "call", "talk", "询问"]):
            return {"intent": "talk", "target": target or ""}
        if any(k in s for k in ["等待", "不动", "沉默", "wait", "skip"]):
            return {"intent": "wait"}
        return None
    def _get_known_names(self) -> list[str]:
        names: list[str] = []
        snap_getter = getattr(self, "_world_snapshot_provider", None)
        if callable(snap_getter):
            try:
                snap = snap_getter() or {}
                chars = (snap.get("characters") or {}).keys()
                names.extend([str(n) for n in chars])
            except Exception:
                pass
        names.extend(["Amiya", "阿米娅", "Kaltsit", "凯尔希", "Doctor", "博士"])  # aliases
        seen = set(); uniq = []
        for n in names:
            if n not in seen:
                seen.add(n); uniq.append(n)
        return uniq
    def _infer_target_from_text(self, text: str) -> Optional[str]:
        low = (text or "").lower()
        mapping = {
            "kaltsit": "Kaltsit", "凯尔希": "Kaltsit", "凯尔希的头": "Kaltsit", "猫头": "Kaltsit",
            "amiya": "Amiya", "阿米娅": "Amiya",
            "doctor": "Doctor", "博士": "Doctor",
        }
        try:
            for k, v in (self._name_map or {}).items():
                mapping[str(k).lower()] = str(v)
        except Exception:
            pass
        for k, v in mapping.items():
            if k in low:
                return v
        for n in self._get_known_names():
            if n and n in (text or ""):
                return n
        return None
    def _ensure_target_or_clarify(self, judged: dict, src_text: str, prev_target: Optional[str] = None, ctx_text: Optional[str] = None) -> dict:
        try:
            intent = judged.get("intent")
            if not isinstance(intent, dict):
                return judged
            kind = str(intent.get("intent") or "").lower()
            requires = {"attack", "talk", "assist", "investigate", "use_item"}
            if kind in requires:
                tgt = intent.get("target")
                if not tgt:
                    t = self._infer_target_from_text(src_text)
                    if t:
                        intent["target"] = t
                        judged["intent"] = intent
                    else:
                        # 松弛：不阻塞澄清，允许后续裁决侧作保守处理
                        if not getattr(self, "_loose_target", False):
                            return {"decision": "clarify", "question": "本次行动需要明确目标对象，请指明目标名称。"}
        except Exception:
            pass
        return judged
    def _find_last_json_object(self, s: str):
        # Simple brace matching to extract last top-level {...}
        try:
            start = -1
            depth = 0
            last_span = None
            for i, ch in enumerate(s):
                if ch == '{':
                    if depth == 0:
                        start = i
                    depth += 1
                elif ch == '}':
                    if depth > 0:
                        depth -= 1
                        if depth == 0 and start >= 0:
                            last_span = (start, i + 1)
            if last_span:
                cand = s[last_span[0]:last_span[1]]
                return json.loads(cand)
        except Exception:
            return None
        return None
    def _fallback_sanitize(self, raw: str) -> str:
        """Heuristic rewriting when model didn't return 'sanitized'.
        Preserve intent but normalize成世界内对白/动作。
        """
        s = (raw or "").strip()
        if not s:
            return "(沉默，示意继续)"
        # Lethal phrasing normalization
        lethal_tokens = ["打死", "弄死", "杀", "毙", "一拳打死"]
        if any(tok in s for tok in lethal_tokens):
            # Convert to lethal attempt description
            return "我猛冲上前，直拳猛击对方要害，意图致命。"
        # Generic strike normalization
        strike_tokens = ["打", "拳", "砸", "击", "捶"]
        if any(tok in s for tok in strike_tokens):
            return "我起身直拳猛击其侧颌，力道十足。"
        # Default: keep concise speech
        if len(s) > 40:
            s = s[:40] + "…"
        return s
    async def _adjudicate_one(self, actor: str, intent: dict) -> List[Msg]:
        from world.tools import (
            attack_roll_dnd, skill_check_dnd, change_relation, advance_time,
            act_dash, act_disengage, act_dodge, act_help, act_hide, act_search,
            act_grapple, act_shove, move_to_band, set_cover, get_cover,
            advantage_for_attack, cover_bonus, clear_condition, has_condition,
            get_turn, get_ac, pop_triggers, use_action,
        )
        msgs: List[Msg] = []
        kind = str(intent.get("intent") or "").lower()
        # Canonicalize common Chinese names to internal IDs
        def _canon(n: str) -> str:
            try:
                if self._name_map and n in self._name_map:
                    return self._name_map[n]
                # also try lowercase key
                lk = n.lower() if isinstance(n, str) else n
                if self._name_map and isinstance(lk, str) and lk in self._name_map:
                    return self._name_map[lk]
            except Exception:
                pass
            # built-in fallbacks
            # normalize common aliases for player
            try:
                nl = n.lower() if isinstance(n, str) else n
                if nl in ("player", "player1", "玩家"):
                    return self.player_name or "Doctor"
            except Exception:
                pass
            mp = {"阿米娅": "Amiya", "凯尔希": "Kaltsit", "博士": "Doctor", "player": self.player_name or "Doctor", "player1": self.player_name or "Doctor"}
            return mp.get(n, n)
        actor_id = _canon(actor)
        narr_meta = {"actor": actor_id, "kind": kind}
        if kind == "attack":
            defender = _canon(intent.get("target") or "")
            ability = (intent.get("ability") or "STR").upper()
            prof = bool(intent.get("proficient") or False)
            dmg_expr = intent.get("damage_expr") or "1d4+STR"
            if defender:
                # advantage and cover
                adv = advantage_for_attack(actor_id, defender)
                ac_base = get_ac(defender)
                ac_bonus, blocked = cover_bonus(defender)
                if blocked:
                    msgs.append(Msg(name="Host", content=f"[裁决] 目标处于完全掩体，无法直接瞄准。", role="assistant"))
                    return msgs
                tr = attack_roll_dnd(actor_id, defender, ability=ability, proficient=prof, damage_expr=dmg_expr, advantage=adv, target_ac=ac_base + ac_bonus)
                lines = self._collect_text_blocks(tr.content)
                try:
                    meta = tr.metadata or {}
                    narr_meta.update({
                        "target": defender,
                        "hit": bool(meta.get("hit")),
                        "damage": int(meta.get("damage_total") or 0),
                        "hp_after": meta.get("hp_after"),
                        "ko": bool(meta.get("hp_after") is not None and int(meta.get("hp_after")) <= 0),
                    })
                except Exception:
                    pass
                # reveal if previously hidden
                try:
                    if has_condition(actor_id, "hidden"):
                        clear_condition(actor_id, "hidden")
                except Exception:
                    pass
                # meta already merged above
                if lines:
                    msgs.append(Msg(name="Host", content=f"[裁决] {actor_id}→{defender}\n" + "\n".join(lines), role="assistant"))
        elif kind == "skill_check":
            skill = str(intent.get("skill") or "perception")
            dc = int(intent.get("dc_hint") or 12)
            tr = skill_check_dnd(actor_id, skill, dc)
            lines = self._collect_text_blocks(tr.content)
            try:
                narr_meta.update({"skill": skill, "success": bool((tr.metadata or {}).get("success"))})
            except Exception:
                pass
            narr_meta.update({"skill": skill, "success": bool((tr.metadata or {}).get("success"))})
            if lines:
                msgs.append(Msg(name="Host", content=f"[检定] {actor_id} {skill} vs DC {dc}\n" + "\n".join(lines), role="assistant"))
        elif kind == "talk":
            target_raw = intent.get("target") or ""
            if target_raw:
                try:
                    narr_meta.update({"target": _canon(target_raw)})
                except Exception:
                    pass
            narr_meta.update({"target": _canon(target_raw) if target_raw else ""})
            rr = self._relation_rules or {}
            delta = int(rr.get("talk_default_delta", 1))
            multi = bool(rr.get("multi_target_split", False))
            if not target_raw:
                if not self._suppress_mech_narration:
                    msgs.append(Msg(name="Host", content=f"[叙述] {actor_id} 与人交谈。", role="assistant"))
            else:
                targets: list[str] = [target_raw]
                if multi:
                    for sep in [",", "，", "、", ";", "；"]:
                        if sep in target_raw:
                            targets = [x.strip() for x in target_raw.split(sep) if x.strip()]
                            break
                applied = False
                for t in targets:
                    tgt = _canon(t)
                    if not delta:
                        continue
                    tr = change_relation(actor_id, tgt, delta, reason="交流")
                    lines = self._collect_text_blocks(tr.content)
                    if lines:
                        msgs.append(Msg(name="Host", content=f"[关系] {actor_id}↔{tgt}: {delta:+d}\n" + "\n".join(lines), role="assistant"))
                        applied = True
                if not applied and delta == 0:
                    if not self._suppress_mech_narration:
                        msgs.append(Msg(name="Host", content=f"[叙述] {actor_id} 与 {target_raw} 交谈。", role="assistant"))
        elif kind == "move":
            subtype = str(intent.get("subtype") or "").lower()
            if subtype == "dash":
                tr = act_dash(actor_id)
                for txt in self._collect_text_blocks(tr.content):
                    msgs.append(Msg(name="Host", content=txt, role="assistant"))
            elif subtype == "disengage":
                tr = act_disengage(actor_id)
                for txt in self._collect_text_blocks(tr.content):
                    msgs.append(Msg(name="Host", content=txt, role="assistant"))
            elif subtype == "take_cover":
                lvl = intent.get("cover") or "half"
                tr = set_cover(actor_id, lvl)
                for txt in self._collect_text_blocks(tr.content):
                    msgs.append(Msg(name="Host", content=txt, role="assistant"))
            else:
                band = str(intent.get("band") or "")
                tgt = _canon(intent.get("target") or "")
                if band and tgt:
                    tr = move_to_band(actor_id, tgt, band)
                    for txt in self._collect_text_blocks(tr.content):
                        msgs.append(Msg(name="Host", content=txt, role="assistant"))
                else:
                    note = intent.get("notes") or "移动至更有利位置"
                    if not self._suppress_mech_narration:
                        msgs.append(Msg(name="Host", content=f"[叙述] {actor_id} {note}", role="assistant"))
        elif kind == "assist":
            tgt = _canon(intent.get("target") or "")
            if tgt:
                tr = act_help(actor_id, tgt)
                for txt in self._collect_text_blocks(tr.content):
                    msgs.append(Msg(name="Host", content=txt, role="assistant"))
        elif kind == "investigate":
            tr = act_search(actor_id, skill=str(intent.get("skill") or "investigation"), dc=int(intent.get("dc_hint") or 12))
            for txt in self._collect_text_blocks(tr.content):
                msgs.append(Msg(name="Host", content=txt, role="assistant"))
        elif kind == "wait":
            subtype = str(intent.get("subtype") or "").lower()
            if subtype == "dodge":
                tr = act_dodge(actor_id)
                for txt in self._collect_text_blocks(tr.content):
                    msgs.append(Msg(name="Host", content=txt, role="assistant"))
            elif subtype == "ready":
                trig = intent.get("trigger") or "接近至可见时"
                from world.tools import act_ready
                tr = act_ready(actor_id, trig, {})
                for txt in self._collect_text_blocks(tr.content):
                    msgs.append(Msg(name="Host", content=txt, role="assistant"))
            else:
                note = intent.get("notes") or "保持待命"
                if not self._suppress_mech_narration:
                    msgs.append(Msg(name="Host", content=f"[待命] {actor_id} {note}", role="assistant"))
        # Special attack maneuvers (grapple/shove)
        if kind == "attack" and str(intent.get("subtype") or "").lower() in ("grapple", "shove"):
            sub = str(intent.get("subtype")).lower()
            tgt = _canon(intent.get("target") or "")
            if tgt:
                tr = act_grapple(actor_id, tgt) if sub == "grapple" else act_shove(actor_id, tgt, mode=str(intent.get("mode") or "prone"))
                for txt in self._collect_text_blocks(tr.content):
                    msgs.append(Msg(name="Host", content=txt, role="assistant"))
        else:
            note = intent.get("notes") or "保持行动"
            if not self._suppress_mech_narration:
                msgs.append(Msg(name="Host", content=f"[叙述] {actor_id} {note}", role="assistant"))
        # Time advancement per action（战斗中跳过分钟推进）
        tc = self._time_cost_min(intent)
        in_combat = False
        try:
            snap = self._world_snapshot_provider() if self._world_snapshot_provider else {}
            in_combat = bool((snap.get("combat") or {}).get("in_combat"))
        except Exception:
            in_combat = False
        if not in_combat:
            try:
                adv = advance_time(tc)
                for txt in self._collect_text_blocks(adv.content):
                    msgs.append(Msg(name="Host", content=txt, role="assistant"))
            except Exception:
                pass
        # Process simple triggers (e.g., OA) after action
        try:
            if in_combat:
                trigs = pop_triggers()
                for trig in trigs:
                    kind = str(trig.get("kind") or "")
                    payload = trig.get("payload") or {}
                    if kind == "opportunity_attack":
                        atk = payload.get("attacker")
                        prov = payload.get("provoker")
                        if not atk or not prov:
                            continue
                        # consume reaction
                        res = use_action(atk, "reaction")
                        if not ((res.metadata or {}).get("ok", False)):
                            msgs.append(Msg(name="Host", content=f"[触发] {atk} 的反应已用，无法进行借机攻击。", role="assistant"))
                            continue
                        adv2 = advantage_for_attack(atk, prov)
                        ac_base = get_ac(prov)
                        ac_bonus, blocked = cover_bonus(prov)
                        # assume melee OA，默认 STR/熟练，1d6+STR
                        tr2 = attack_roll_dnd(atk, prov, ability="STR", proficient=True, damage_expr="1d6+STR", advantage=adv2, target_ac=ac_base + ac_bonus)
                        lines = self._collect_text_blocks(tr2.content)
                        if lines:
                            msgs.append(Msg(name="Host", content=f"[借机攻击] {atk}→{prov}\n" + "\n".join(lines), role="assistant"))
        except Exception:
            pass
        # Atmosphere tuning and micro-narration (pure sentence, no labels)
        try:
            from world.tools import adjust_tension
            if kind == "attack":
                dval = int(narr_meta.get("damage") or 0)
                if narr_meta.get("hit"):
                    adjust_tension(2 if dval >= 6 else 1)
            elif kind == "talk":
                adjust_tension(-1)
        except Exception:
            pass
        try:
            if self._narrator is not None and callable(getattr(self._narrator, "generate", None)):
                narr_meta["time_cost"] = int(tc)
                snap = self._world_snapshot_provider() if self._world_snapshot_provider else {}
                text = await self._narrator.generate(narr_meta, snap or {})
                if text:
                    msgs.append(Msg(name="Host", content=str(text), role="assistant"))
        except Exception:
            pass
        return msgs
    def _time_cost_min(self, intent: dict) -> int:
        """Decide how many in-world minutes an intent should cost.
        Priority: explicit fields in intent -> default mapping.
        """
        for key in ("time_cost", "time_min"):
            try:
                if key in intent and intent[key] is not None:
                    v = int(intent[key])
                    if v >= 0:
                        return v
            except Exception:
                pass
        kind = str(intent.get("intent") or "").lower()
        rules = self._time_rules or {}
        try:
            mapping = rules.get("intent_cost_min") or {}
            if kind in mapping:
                return int(mapping[kind])
            if "default_min" in rules:
                return int(rules["default_min"])
        except Exception:
            pass
        if kind in ("attack", "talk", "move", "assist", "wait"):
            return 1
        if kind in ("investigate", "skill_check"):
            return 3
        return 1
    def _collect_text_blocks(self, blocks) -> list[str]:
        lines: list[str] = []
        for blk in (blocks or []):
            if isinstance(blk, dict):
                if blk.get("type") == "text":
                    t = blk.get("text")
                    if isinstance(t, str) and t:
                        lines.append(t)
            else:
                ttype = getattr(blk, "type", None)
                t = getattr(blk, "text", None)
                if ttype == "text" and isinstance(t, str) and t:
                    lines.append(t)
        return lines

    # ------------------- Director (Spawn) -------------------
    async def consider_director_actions(self) -> dict:
        """Let KP act as a lightweight director: decide whether to spawn enemies or do nothing.
        Returns a dict (JSON-like):
        {"decision":"none|spawn", "why":"...", "broadcast":"...",
         "spawn":[{"name":"RI_Raider1","kind":"raider","target_pref":"Doctor",
                    "ac":13,"hp":9,
                    "abilities":{"STR":12,"DEX":12,"CON":12,"INT":8,"WIS":10,"CHA":8},
                    "damage_expr":"1d6+STR","persona":"..."}]}
        """
        # First: try story-driven actions (rules). If any, return directly.
        story_dec = self._story_decision()
        if isinstance(story_dec, dict) and story_dec.get("decision") in ("actions", "spawn"):
            return story_dec

        # Strict mode: disable LLM-driven spawn fallback
        if getattr(self, "_strict_spawn", False):
            return {"decision": "none"}

        # Prepare context for the model
        world_text = self._format_world_snapshot()
        ctx_text = self._build_context_text(10)
        sys = (
            "你是KP-导演，负责根据剧情需要在合适的时机插入外部要素（敌人/巡逻/事故）。\n"
            "输出严格JSON，不要任何多余文字/标点/markdown。\n"
            "必须从以下决定中选择其一：\n"
            "- decision=none（不插入）\n"
            "- decision=spawn（插入一组敌人/巡逻）\n"
            "规则：\n"
            "- 插入必须服务于当前目标推进或制造合理阻力，避免突兀。\n"
            "- 避免太频繁插入；若刚刚发生过冲突/推进，可选择none。\n"
            "- 若选择spawn，请给出：广播文案、每个单位的name/kind/目标偏好/属性与伤害表达式/简短persona。\n"
            "- persona用中文，风格简洁；建议敌人的attack JSON包含 damage_expr；通常伤害如1d6+STR。\n"
        )
        usr = (
            f"世界快照：\n{world_text}\n\n"
            f"最近上下文：\n{ctx_text}\n\n"
            f"剧情偏好：{self._director_policy}\n"
            "请仅返回JSON：{\n"
            "  \"decision\": \"none|spawn\",\n"
            "  \"why\": \"简要原因\",\n"
            "  \"broadcast\": \"若spawn时，给一段入场叙述\",\n"
            "  \"spawn\": [ { \"name\": \"?\", \"kind\": \"raider|guard|sniper|patrol\", \n"
            "               \"target_pref\": \"Doctor|nearest|lowest_hp\", \"ac\": 13, \"hp\": 9, \n"
            "               \"abilities\": {\"STR\":12,\"DEX\":12,\"CON\":12,\"INT\":8,\"WIS\":10,\"CHA\":8}, \n"
            "               \"damage_expr\": \"1d6+STR\", \"persona\": \"中文人设\" } ]\n"
            "}"
        )
        try:
            res = await self.model([
                {"role": "system", "content": sys},
                {"role": "user", "content": usr},
            ])
            text = None
            get_text = getattr(res, "get_text_content", None)
            if callable(get_text):
                text = get_text()
            if not text:
                text = getattr(res, "content", None)
                if isinstance(text, list):
                    parts = []
                    for blk in text:
                        if isinstance(blk, dict) and blk.get("type") == "text":
                            parts.append(blk.get("text", ""))
                    text = "".join(parts)
                elif not isinstance(text, str):
                    text = None
            if not text:
                return {"decision": "none"}
            import json as _json
            obj = _json.loads(self._strip_code_fences(text))
            if not isinstance(obj, dict):
                return {"decision": "none"}
            # Shallow sanitize
            dec = str(obj.get("decision", "none")).lower()
            if dec not in ("none", "spawn"):
                dec = "none"
            obj["decision"] = dec
            if dec == "spawn":
                sp = obj.get("spawn")
                if not isinstance(sp, list) or not sp:
                    obj["decision"] = "none"
            return obj
        except Exception:
            return {"decision": "none"}

    def set_director_policy(self, text: str) -> None:
        self._director_policy = str(text or "").strip() or self._director_policy

    def set_story(self, story: dict) -> None:
        """Attach a structured story script with acts/beats and simple conditions."""
        try:
            self._story = story or {}
            self._fired_beats = set()
            self._beat_last_min = {}
            self._stalled_rounds = 0
            self._last_obj_status_sig = ""
        except Exception:
            self._story = None

    def set_kp_system_prompt(self, text: str) -> None:
        """Override KP system prompt if a custom prompt is provided."""
        try:
            t = (text or "").strip()
            if t:
                self._sys_prompt = t
        except Exception:
            pass

    def set_name_map(self, name_map: dict) -> None:
        """Set a custom name canonicalization mapping (keys can be any alias)."""
        try:
            m: dict[str, str] = {}
            for k, v in (name_map or {}).items():
                if not k or not v:
                    continue
                m[str(k)] = str(v)
            if m:
                self._name_map.update(m)
        except Exception:
            pass

    def set_model_config(self, cfg: dict) -> None:
        """Rebuild KP's model client from a model config dict.
        cfg example: {"base_url":"...","kp":{"model":"...","temperature":0.2,"stream":false}}
        """
        try:
            import os
            root = cfg or {}
            sec = root.get("kp") or root
            model_name = sec.get("model") or os.getenv("KIMI_MODEL", "kimi-k2-turbo-preview")
            base_url = root.get("base_url") or os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
            temperature = float(sec.get("temperature", 0.2))
            stream = bool(sec.get("stream", False))
            self.model = OpenAIChatModel(
                model_name=model_name,
                api_key=os.environ["MOONSHOT_API_KEY"],
                stream=stream,
                client_args={"base_url": base_url},
                generate_kwargs={"temperature": temperature},
            )
        except Exception:
            pass

    def set_time_rules(self, rules: dict) -> None:
        self._time_rules = rules or None

    def set_relation_rules(self, rules: dict) -> None:
        self._relation_rules = rules or None

    def set_narrator(self, narrator) -> None:
        self._narrator = narrator

    def set_feature_flags(self, flags: dict) -> None:
        try:
            if isinstance(flags, dict):
                val = flags.get("suppress_mech_narration")
                if val is not None:
                    self._suppress_mech_narration = bool(val)
                v_strict_spawn = flags.get("strict_spawn")
                if v_strict_spawn is not None:
                    self._strict_spawn = bool(v_strict_spawn)
                v_auto = flags.get("kp_auto_accept")
                if v_auto is not None:
                    self._auto_accept = bool(v_auto)
                v_loose = flags.get("kp_loose_target")
                if v_loose is not None:
                    self._loose_target = bool(v_loose)
                v_pres = flags.get("kp_preserve_text")
                if v_pres is not None:
                    self._preserve_text = bool(v_pres)
        except Exception:
            pass

    def _story_decision(self) -> dict:
        """Evaluate story beats and return a director decision.
        Supported beat.when keys: time_min_gte, objectives_pending_any (list of names),
        rounds_stalled_gte (int), cooldown_min (int), once (bool).
        Beat.actions supports: broadcast, spawn (units: list of unit specs), add_objective,
        complete_objective, block_objective, schedule_event, relation, grant, damage, heal.
        Returns a dict with decision 'actions' and an 'actions' list if any beat matches.
        """
        if not self._story:
            return {"decision": "none"}
        try:
            snap = self._world_snapshot_provider() if self._world_snapshot_provider else {}
        except Exception:
            snap = {}
        # Track 'stalled rounds' by objective status signature
        try:
            st = snap.get("objective_status", {}) or {}
            sig = ";".join(f"{k}:{v}" for k, v in sorted(st.items()))
        except Exception:
            st = {}; sig = ""
        if sig == self._last_obj_status_sig:
            self._stalled_rounds += 1
        else:
            self._stalled_rounds = 0
            self._last_obj_status_sig = sig

        def _ok_when(when: dict) -> bool:
            if not isinstance(when, dict):
                return True
            # time_min_gte
            try:
                tmin = int(snap.get("time_min") or 0)
                need = when.get("time_min_gte")
                if need is not None and tmin < int(need):
                    return False
            except Exception:
                pass
            # objectives_pending_any
            pend = when.get("objectives_pending_any")
            if isinstance(pend, list) and pend:
                m = snap.get("objective_status", {}) or {}
                if not any(m.get(str(x)) == "pending" for x in pend):
                    return False
            # rounds_stalled_gte
            try:
                need_stall = when.get("rounds_stalled_gte")
                if need_stall is not None and self._stalled_rounds < int(need_stall):
                    return False
            except Exception:
                pass
            # cooldown_min per beat handled outside
            return True

        acts = (self._story.get("acts") or []) if isinstance(self._story, dict) else []
        for act in acts:
            beats = (act.get("beats") or []) if isinstance(act, dict) else []
            for beat in beats:
                bid = str(beat.get("id") or "")
                when = beat.get("when") or {}
                once = bool(beat.get("once", False))
                cooldown_min = beat.get("cooldown_min")
                if once and bid in self._fired_beats:
                    continue
                if not _ok_when(when):
                    continue
                # cooldown check
                try:
                    if cooldown_min is not None:
                        last = int(self._beat_last_min.get(bid, -10**9))
                        cur = int(snap.get("time_min") or 0)
                        if cur - last < int(cooldown_min):
                            continue
                except Exception:
                    pass
                actions = beat.get("actions") or []
                if not isinstance(actions, list) or not actions:
                    continue
                # mark fired
                self._fired_beats.add(bid)
                try:
                    self._beat_last_min[bid] = int(snap.get("time_min") or 0)
                except Exception:
                    pass
                return {"decision": "actions", "why": f"beat:{bid}", "actions": actions}
        return {"decision": "none"}
