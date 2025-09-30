"""
PlayerAgent: a human-in-the-loop agent that lets a real player speak
as one of the participants in MsgHub.
"""
from __future__ import annotations
import asyncio
from typing import List

from agentscope.agent import AgentBase  # type: ignore
from agentscope.message import Msg  # type: ignore


class PlayerAgent(AgentBase):
    def __init__(self, name: str = "Player", prompt: str = "你> ") -> None:
        super().__init__()
        self.name = name
        self.prompt = prompt
        self.transcript: List[Msg] = []

    async def observe(self, msg: Msg | List[Msg] | None) -> None:
        if msg is None:
            return
        if isinstance(msg, list):
            self.transcript.extend(msg)
        else:
            self.transcript.append(msg)

    async def reply(self, msg: Msg | List[Msg] | None = None) -> Msg:
        # Ask for player's input without blocking the event loop
        loop = asyncio.get_running_loop()
        try:
            line: str = await loop.run_in_executor(None, input, self.prompt)
        except (EOFError, KeyboardInterrupt):
            line = "(玩家沉默)"

        # Minimal slash-commands (optional)
        #   /quit -> say goodbye
        if line.strip() == "/quit":
            line = "我先告辞了，回头见。"

        out = Msg(name=self.name, content=line, role="user")
        await self.print(out)
        return out

    async def handle_interrupt(self, *args, **kwargs) -> Msg:
        msg = Msg(name=self.name, content="(玩家被中断)", role="user")
        await self.print(msg)
        return msg
