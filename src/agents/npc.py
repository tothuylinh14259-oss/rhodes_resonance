"""
Minimal Agentscope-native NPC agent implementing AgentBase.
Compatible with MsgHub + sequential_pipeline.
"""
from __future__ import annotations
from typing import List
from dataclasses import dataclass

from agentscope.agent import AgentBase  # type: ignore
from agentscope.message import Msg  # type: ignore


@dataclass
class SimpleNPCAgent(AgentBase):
    name: str
    persona: str
    style_hint: str = ""

    def __post_init__(self):
        # AgentBase is not a dataclass; we must init it explicitly.
        AgentBase.__init__(self)
        self.introduced: bool = False
        self.transcript: List[Msg] = []

    async def observe(self, msg: Msg | List[Msg] | None) -> None:
        # Collect observed messages into local transcript
        if msg is None:
            return
        if isinstance(msg, list):
            self.transcript.extend(msg)
        else:
            self.transcript.append(msg)

    async def reply(self, msg: Msg | List[Msg] | None = None) -> Msg:
        # First turn: self introduction
        if not self.introduced:
            self.introduced = True
            reply_msg = Msg(
                name=self.name,
                content=(
                    f"我是{self.name}。{self.persona} 很高兴认识你们。{self.style_hint}".strip()
                ),
                role="assistant",
            )
            await self.print(reply_msg)
            return reply_msg

        # Otherwise, react to last observed message
        last = self.transcript[-1] if self.transcript else None
        last_text = (last.get_text_content() if last else "") or ""
        last_text_low = last_text.lower()

        if any(k in last_text_low for k in ["price", "价格", "报价"]):
            txt = f"{self.name}：要看材料和工时，先说说你需要什么。"
        elif any(k in last_text_low for k in ["help", "帮助", "帮忙"]):
            txt = f"{self.name}：当然可以，具体需要我做什么？"
        elif any(k in last_text_low for k in ["bye", "晚安", "告辞", "再见"]):
            txt = f"{self.name}：路上小心，我们后会有期。"
        elif any(k in last_text_low for k in ["天气", "weather"]):
            txt = f"{self.name}：今天的风很温柔，适合出门。"
        else:
            txt = f"{self.name}：最近镇上倒是安生，你们从哪来？"

        reply_msg = Msg(name=self.name, content=txt, role="assistant")
        await self.print(reply_msg)
        return reply_msg

    async def handle_interrupt(self, *args, **kwargs) -> Msg:
        # Basic interrupt handling: return a short message
        msg = Msg(name=self.name, content=f"{self.name}：稍后继续。", role="assistant")
        await self.print(msg)
        return msg
