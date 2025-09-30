# 项目规范（NPC 群聊与剧情驱动 Demo）

目标
- 演示以 MsgHub 管理多 NPC 对话，支持轮流发言与动态加入/退出。
- 提供可扩展骨架：后续可接入真实 AgentScope、工具调用、长时记忆、剧情节点图与游戏引擎。

架构角色
- Director（可选）：控制回合、工具调用白名单、剧情节点跳转（本 Demo 先由 `main.py` 代行部分职责）。
- NPC Agent：基于人设产出发言；本 Demo 使用 `SimpleNPCAgent`（无 LLM，确定性），便于离线运行。
- MsgHub：集中管理参与者与消息序列；`sequential_pipeline` 实现按顺序发言。
- World/Tools：世界状态与规则工具（时间推进、关系变化、物品发放等）。

接口与约定
- 消息对象：`Msg(sender: str, content: str, role: str='assistant')`。
- Hub 生命周期：
  - `async with MsgHub(participants=[...], announcement=Msg(...)) as hub:`
  - `hub.add(agent)`, `hub.delete(agent)`, `await hub.broadcast(Msg(...))`。
  - `await sequential_pipeline([agent_a, agent_b, ...])` 在 Hub 上下文内调用。
- Agent 接口：
  - `await agent.step(transcript: list[Msg]) -> Msg | str | dict`。
  - 本 Demo 中 `dict` 形如 `{ "speak": "一句对白" }`；实际项目建议统一 JSON Schema（见下）。

建议的结构化输出 Schema（后续接入 LLM 时启用）
```json
{
  "speak": "string",
  "emotion": "calm|angry|curious|...",
  "intents": ["ask_price", "seek_help"],
  "tool_calls": [
    {"name": "change_relation", "args": {"a": "Warrior", "b": "Mage", "delta": 1, "reason": "..."}}
  ]
}
```
- 校验：若解析失败或违反约束（长度、禁区），应触发重试与“自我收敛”提示（后续加入）。

工具规范（World/Tools）
- 纯函数优先：幂等/可回滚；返回 `{ ok: bool, ... }`。
- 变更需附理由：如 `reason` 字段，便于追踪和回放。
- 示例工具：`advance_time(mins)`, `change_relation(a,b,delta,reason)`, `grant_item(target,item,n)`。

剧情节点（后续扩展）
- 节点定义：`id, conditions, announcement, participants, allowed_tools, next_nodes`。
- 运行时：每回合评估 `conditions` 决定跳转；Director 产出 `join/leave/broadcast` 指令。

记忆（后续扩展）
- 短时：当前场景对话上下文（可做摘要）。
- 长时：角色人设、关系走向、世界事实摘要；场景结束写入；按话题/关系检索 Top-K。

目录结构
```
src/
  main.py                # 入口：酒馆场景 Demo
  mini_agentscope/       # 本地最小替身（无依赖），接口贴近 AgentScope
    message.py
    pipeline.py
  agents/
    npc.py               # 简单 NPC Agent（确定性）
  world/
    tools.py             # 世界状态与工具
```

代码风格
- Python 3.10+；类型注解；ASCII 文件（中文仅限文档、对话内容）。
- 函数短小、返回结构化数据；关键路径处保留简短注释说明意图。

运行与环境
- 使用 Conda 环境（无降级/无本地 stub）：
  - `conda env create -f environment.yml && conda activate npc-talk`
  - `python src/main.py`
- 该环境要求真实 AgentScope（含 `agentscope.pipeline`）。
- 针对真实模型与工具调用，需配置相应环境变量（如 DASHSCOPE_API_KEY 等）。

演进路线
- M0：当前 Demo（轮流发言 + 动态加入 + 世界状态更新）。
- M1：引入结构化输出校验与重试；关系图可视化；节点图驱动。
- M2：接入游戏引擎（HTTP/MCP 工具）；事件总线驱动剧情。
- M3：跟踪与回放（tracing）、成本与一致性调优、自动化测试。
