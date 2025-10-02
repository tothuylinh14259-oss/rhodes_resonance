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
            # Strict confirmation: only '/yes' confirms; anything else is treated as new intent
            if raw.strip() == "/yes":
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
            # Treat as new intent: re-judge and propose a new sanitized version, ask to reply '/yes'
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
            sanitized = judged.get("sanitized") or (player_msg.get_text_content() or "")
            self._awaiting_player = True
            self._awaiting_confirm = True
            self._pending_sanitized = sanitized
            self._pending_intent = judged.get("intent") if isinstance(judged.get("intent"), dict) else None
            confirm = Msg(name=self.name, content=f"我理解为：{sanitized}。若正确请回复 /yes 确认。", role="assistant")
            await self.print(confirm)
            return confirm
        if decision == "clarify":
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
            out_msgs.extend(self._adjudicate_one(actor, it))
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
        for k, v in {"amiya": "Amiya", "阿米娅": "Amiya", "kaltsit": "Kaltsit", "凯尔希": "Kaltsit"}.items():
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
            "kaltsit": "Kaltsit", "凯尔希": "Kaltsit",
            "amiya": "Amiya", "阿米娅": "Amiya",
            "doctor": "Doctor", "博士": "Doctor",
        }
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
    def _adjudicate_one(self, actor: str, intent: dict) -> List[Msg]:
        from world.tools import attack_roll_dnd, skill_check_dnd, change_relation, advance_time
        msgs: List[Msg] = []
        kind = str(intent.get("intent") or "").lower()
        # Canonicalize common Chinese names to internal IDs
        def _canon(n: str) -> str:
            mp = {"阿米娅": "Amiya", "凯尔希": "Kaltsit", "博士": "Doctor"}
            return mp.get(n, n)
        actor_id = _canon(actor)
        if kind == "attack":
            defender = _canon(intent.get("target") or "")
            ability = (intent.get("ability") or "STR").upper()
            prof = bool(intent.get("proficient") or False)
            dmg_expr = intent.get("damage_expr") or "1d4+STR"
            if defender:
                tr = attack_roll_dnd(actor_id, defender, ability=ability, proficient=prof, damage_expr=dmg_expr)
                lines = self._collect_text_blocks(tr.content)
                if lines:
                    msgs.append(Msg(name="Host", content=f"[裁决] {actor_id}→{defender}\n" + "\n".join(lines), role="assistant"))
        elif kind == "skill_check":
            skill = str(intent.get("skill") or "perception")
            dc = int(intent.get("dc_hint") or 12)
            tr = skill_check_dnd(actor_id, skill, dc)
            lines = self._collect_text_blocks(tr.content)
            if lines:
                msgs.append(Msg(name="Host", content=f"[检定] {actor_id} {skill} vs DC {dc}\n" + "\n".join(lines), role="assistant"))
        elif kind == "talk":
            target = _canon(intent.get("target") or "")
            if target:
                tr = change_relation(actor_id, target, 1, reason="积极交流")
                lines = self._collect_text_blocks(tr.content)
                if lines:
                    msgs.append(Msg(name="Host", content=f"[关系] {actor_id}↔{target}: +1\n" + "\n".join(lines), role="assistant"))
            else:
                msgs.append(Msg(name="Host", content=f"[叙述] {actor_id} 与人交谈。", role="assistant"))
        elif kind in ("move", "wait", "assist", "investigate"):
            note = intent.get("notes") or kind
            msgs.append(Msg(name="Host", content=f"[叙述] {actor_id} {note}", role="assistant"))
        else:
            note = intent.get("notes") or "保持行动"
            msgs.append(Msg(name="Host", content=f"[叙述] {actor_id} {note}", role="assistant"))
        # Time advancement per action
        tc = self._time_cost_min(intent)
        try:
            adv = advance_time(tc)
            for txt in self._collect_text_blocks(adv.content):
                msgs.append(Msg(name="Host", content=txt, role="assistant"))
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
