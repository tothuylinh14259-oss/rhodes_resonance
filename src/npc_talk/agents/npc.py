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

    async def handle_interrupt(self, *args, **kwargs) -> Msg:
        # Basic interrupt handling: return a short message
        msg = Msg(name=self.name, content=f"{self.name}：稍后继续。", role="assistant")
        await self.print(msg)
        return msg
