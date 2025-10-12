# 项目规范（NPC 群聊与剧情驱动 Demo）

目标
- 以 MsgHub 管理 NPC 群聊，支持轮流发言与动态插入事件/敌人。
- 提供可扩展骨架：旁白、D&D 检定/攻击、剧情节拍与配置化（已移除 KP/裁决）。

组件角色
- KP：已移除（本分支不包含主持/裁决/导演能力）。
- NPC Agent：由 LLM 生成对白，并直接通过 CALL_TOOL 调用工具（携带 JSON 参数）。
- MsgHub：集中管理参与者与消息；`sequential_pipeline` 顺序发言。
- World/Tools：世界状态与规则工具（时间、关系、物品、D&D、事件时钟、目标）。
- Narrator：在关键动作后生成中文微叙事（环境/感官白描，避免复述人物）。

接口与约定
- 消息对象：`Msg(sender: str, content: str|List[Block], role: 'assistant'|'user')`。
- 握手：KP 已移除；NPC 直接输出对白与 CALL_TOOL 工具调用。
- NPC 行动：`await agent.step(transcript)->Msg|str|dict`；建议直接输出对白与 CALL_TOOL。
- 裁决：已移除；当前仅展示 NPC 对白与工具调用，不进行裁决/导演动作。

工具调用格式（CALL_TOOL）
```
CALL_TOOL tool_name({json})

# 也支持：
CALL_TOOL tool_name
{json}
```
- 示例：
```
阿米娅压低声音：‘靠近目标位置。’
CALL_TOOL advance_position({"name": "Amiya", "target": {"x": 1, "y": 1}, "steps": 2, "reason": "接近掩体"})

CALL_TOOL perform_attack({"attacker": "Amiya", "defender": "Rogue", "weapon": "amiya_focus", "reason": "压制敌人"})
```
- 约定：每次调用工具参数 JSON 必须包含 `reason` 字段，用一句话说明行动理由；若缺省可记录为“未提供”。

工具规范
- 纯函数优先、幂等可回放；返回 `ToolResponse({blocks}, {metadata})`。
- 变更附理由：如关系/目标变化记录 `reason/note`，便于追踪。
- D&D 检定/攻击：`skill_check_dnd`、`saving_throw_dnd`、`attack_roll_dnd`（支持 advantage、熟练、能力修正、伤害表达式）。
- 事件时钟：`schedule_event` + `process_events`（由 `advance_time` 自动触发）。
- 目标管理：`add_objective / complete_objective / block_objective`。

目录结构
```
src/
  main.py             # 兼容入口
  npc_talk/
    cli.py            # 真正入口
    app.py            # 回合驱动
    agents/
      npc.py          # 简单 Agent 基类实现（目前未用）
      narrator.py     # 旁白
      factory.py      # Agent 构造
    world/
      tools.py        # 世界状态与工具
configs/               # 角色、模型、提示词、旁白策略、规则、剧情设定
  story.json          # 场景/位置/剧情节拍
docs/
```

运行与环境
- Python 3.11；真实 Agentscope（`agentscope.pipeline`）
- LLM：通过 Kimi 的 OpenAI 兼容接口（需 `MOONSHOT_API_KEY`；可配 `KIMI_BASE_URL/KIMI_MODEL`）
- 运行：`conda env create -f environment.yml && conda activate npc-talk && python src/main.py`

演进路线
- M0：当前 Demo（回合 + 握手 + 审判 + 旁白 + 事件时钟）
- M1：结构化输出校验与重试；关系/目标可视化；剧情图驱动
- M2：接入游戏引擎（HTTP/MCP 工具）；事件总线驱动
- M3：Tracing/回放、成本与一致性调优、自动化测试
