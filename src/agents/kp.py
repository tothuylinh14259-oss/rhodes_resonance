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
    "你是KP（守秘人/主持人），负责将玩家在中世纪奇幻酒馆场景中的发言/行动，"
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
    "输出严格JSON（不要markdown围栏/额外文字）：\n"
    "{\n"
    "  \"decision\": \"accept|clarify\",\n"
    "  \"sanitized\": \"当decision=accept时，给出改写后的玩家对白/行动（1-2句）。\",\n"
    "  \"question\": \"当decision=clarify时，提出一个具体且简短的问题。\"\n"
    "}"
)


class KPAgent(AgentBase):
    def __init__(self, name: str = "KP", player_persona: str | None = None) -> None:
        super().__init__()
        self.name = name
        self.transcript: List[Msg] = []
        self._last_processed_player_id: Optional[str] = None
        self._awaiting_player: bool = False
        self._awaiting_confirm: bool = False
        self._pending_sanitized: Optional[str] = None
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
            norm = self._normalize_confirm_text(raw)
            if self._is_yes(norm):
                # finalize: broadcast sanitized as Player
                final_msg = Msg(name="Player", content=self._pending_sanitized or raw, role="user")
                await self.print(final_msg)
                # reset state
                self._awaiting_confirm = False
                self._awaiting_player = False
                self._pending_sanitized = None
                self._last_processed_player_id = player_msg.id
                return final_msg
            if self._is_no(norm):
                self._awaiting_confirm = False
                self._awaiting_player = True
                ask = Msg(name=self.name, content="那请用一句话重新描述你的意图或对白。", role="assistant")
                await self.print(ask)
                return ask
            # Treat as new intent: re-judge and propose a new sanitized version
            judged2 = await self._judge_player_input(player_msg)
            sanitized2 = judged2.get("sanitized") or raw
            self._pending_sanitized = sanitized2
            self._awaiting_confirm = True
            self._awaiting_player = True
            confirm_new = Msg(name=self.name, content=f"我理解为：{sanitized2}。是否确认？（是/否）", role="assistant")
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
            confirm = Msg(name=self.name, content=f"我理解为：{sanitized}。是否确认？（是/否）", role="assistant")
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
        confirm2 = Msg(name=self.name, content=f"我理解为：{self._pending_sanitized}。是否确认？（是/否）", role="assistant")
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
            if m.name == "Player" and m.role == "user":
                return m
        return None

    async def _judge_player_input(self, player_msg: Msg) -> dict:
        # Build chat messages for the OpenAI-compatible API with context
        content = player_msg.get_text_content() or ""
        ctx_text = self._build_context_text(max_items=8)
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
            return json.loads(self._strip_code_fences(text))
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

    def _build_context_text(self, max_items: int = 8) -> str:
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
        final_msg = Msg(name="Player", content=sanitized, role="user")
        await self.print(final_msg)
        return final_msg
