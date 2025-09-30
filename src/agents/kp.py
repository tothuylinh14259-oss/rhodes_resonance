"""
KPAgent: a Game Master agent that validates and normalizes the Player's input
so it fits the world rules, and only then forwards it to others.

Behavior:
- If the latest Player message is acceptable, emit a sanitized Player message
  (Msg with name='Player', role='user').
- If ambiguous, ask a concise clarification question as KP.
- If impossible/out-of-world, reject and suggest an alternative as KP.

This agent uses Kimi (OpenAI-compatible) via Agentscope OpenAIChatModel.
"""
from __future__ import annotations
import json
from typing import List, Optional

from agentscope.agent import AgentBase  # type: ignore
from agentscope.message import Msg  # type: ignore
from agentscope.model import OpenAIChatModel  # type: ignore


_SYSTEM_PROMPT = (
    "你是KP（守秘人/主持人），负责校对玩家在中世纪奇幻酒馆场景中的发言与行动，使其符合世界观与常识。\n"
    "世界规则（示例）：\n"
    "- 无现代科技（手机/枪支/无人机/电器等），没有瞬间传送。\n"
    "- 常识一致（角色只知道自己经历；不能越权知道他人隐私或未来）。\n"
    "- 行动应具体、可执行、短小；避免一回合内完成过多复杂行动。\n"
    "- 角色个性与动机应合理；避免违背已知设定。\n"
    "请对玩家的最新输入进行判定，并用JSON输出：\n"
    "{\n"
    "  \"decision\": \"accept|clarify|reject\",\n"
    "  \"sanitized\": \"当decision=accept时，给出规范后的简短发言/行动（1-2句）。\",\n"
    "  \"question\": \"当decision=clarify时，向玩家提出一个具体而简短的澄清问题。\",\n"
    "  \"reason\": \"当decision=reject时，说明不符合点。\",\n"
    "  \"suggestion\": \"当decision=reject时，给出1条可接受的替代建议。\"\n"
    "}\n"
    "只输出JSON，不要附加其它文字。"
)


class KPAgent(AgentBase):
    def __init__(self, name: str = "KP") -> None:
        super().__init__()
        self.name = name
        self.transcript: List[Msg] = []
        self._last_processed_player_id: Optional[str] = None

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

        # If this player message is already processed, acknowledge
        if player_msg.id == self._last_processed_player_id:
            out = Msg(name=self.name, content="（已确认上一条输入）", role="assistant")
            await self.print(out)
            return out

        # Call Kimi to judge the latest player input
        judged = await self._judge_player_input(player_msg)
        decision = judged.get("decision")

        if decision == "accept":
            self._last_processed_player_id = player_msg.id
            sanitized = judged.get("sanitized") or player_msg.get_text_content() or ""
            final_msg = Msg(name="Player", content=sanitized, role="user")
            await self.print(final_msg)
            return final_msg

        if decision == "clarify":
            q = judged.get("question") or "请更具体说明你的行动。"
            ask = Msg(name=self.name, content=q, role="assistant")
            await self.print(ask)
            return ask

        # reject or fallback
        reason = judged.get("reason") or "不符合世界观。"
        suggestion = judged.get("suggestion") or "请给出更具体且合理的行动。"
        warn = Msg(name=self.name, content=f"{reason} 建议：{suggestion}", role="assistant")
        await self.print(warn)
        return warn

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
        # Build minimal chat messages for the OpenAI-compatible API
        content = player_msg.get_text_content() or ""
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"玩家输入：{content}"},
        ]
        res = await self.model(messages)
        text = res.get_text_content() if hasattr(res, "get_text_content") else None
        if not text:
            return {"decision": "clarify", "question": "请简要说明你的行动目标与方式。"}
        # Try parse JSON
        try:
            return json.loads(text)
        except Exception:
            # Try to repair common formatting issues
            text_stripped = text.strip()
            if text_stripped.startswith("```) "):
                text_stripped = text_stripped.strip("`\n ")
            try:
                return json.loads(text_stripped)
            except Exception:
                return {"decision": "clarify", "question": "请用更具体、可执行的描述表达你的行动。"}
