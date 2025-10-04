# NPC Talk Demo (Agentscope)

NPC 群聊（无 KP）+ 微旁白的最小可跑通 Demo。基于真实 Agentscope（`agentscope.pipeline`），通过 Kimi 的 OpenAI 兼容接口驱动 LLM NPC。

## Quick Start（推荐 Conda）

```bash
# 一次性：创建并激活环境
cd /Users/administrator/syncdisk/npc_talk_demo
conda env create -f environment.yml
conda activate npc-talk

# 必填：Kimi API Key
export MOONSHOT_API_KEY=你的Kimi密钥
# 可选：自定义 Kimi 接口与模型（如需）
export KIMI_BASE_URL=https://api.moonshot.cn/v1
export KIMI_MODEL=kimi-k2-turbo-preview

# 运行
python src/main.py      # 兼容入口（推荐）
# 或（已打包为模块，需将 src 加入路径或安装本包）
# PYTHONPATH=src python -m npc_talk.cli
```

运行期望：两个 NPC 在“旧城区·北侧仓棚”进行回合制对话，每回合输出对白与一个意图 JSON（不执行裁决/导演动作）；过程写入 `run.log`。

## 目录结构（已重构）

```
npc_talk_demo/
  src/
    main.py               # 兼容入口（薄封装，转发到 npc_talk.cli:main）
    npc_talk/
      cli.py             # 真正入口（日志配置 + 运行 demo）
      app.py             # 回合驱动、广播逻辑（无 KP/裁决/导演）
      config.py          # 配置加载与项目路径工具
      agents/
        npc.py           # SimpleNPCAgent（保留最简基类实现，当前未用）
        narrator.py      # 旁白（LLM 生成微叙事）
        factory.py       # Kimi ReActAgent 构造器（系统提示拼装）
      world/
        tools.py         # 世界状态与工具（时间、关系、物品、D&D、事件时钟…）
  configs/
    characters.json       # 参与者/人设/D&D数值/出场顺序
    model.json            # LLM 接入（base_url、npc 模型等）
    prompts.json          # 可选：玩家人设、NPC提示词模板、名称映射（示例见 prompts.json.example）
    narration_policy.json # 旁白策略（长度/候选/焦点轮换…）
    narration_env.json    # 场景关键词（visual/sound/air/props）
    time_rules.json       # 意图用时规则
    relation_rules.json   # 关系变更规则
    feature_flags.json    # 特性开关
  docs/
    spec.md               # 项目规范（设计/接口/约定）
    plot.story.json       # 可选：剧情节拍（当前不启用导演）
  environment.yml         # Conda 环境（Python 3.11 + Agentscope）
  pyproject.toml          # 打包/开发工具（ruff/mypy/pytest），可 `pip install -e .[dev]`
  run.log                 # 运行日志（每次覆盖）
```

## 运行时交互

- 本分支已移除 KP/玩家输入流程，仅 NPC 轮流行动，输出对白与一个意图 JSON。
- 每回合流程（简化）：
  1) 主持信息 + 世界概要
  2) NPC 依序行动（不进行裁决/导演动作）
  3) 回合推进（默认最多 3 回合后终止）

## 必要环境变量

- `MOONSHOT_API_KEY`（必填）：Kimi API Key
- `KIMI_BASE_URL`（可选，默认 `https://api.moonshot.cn/v1`）
- `KIMI_MODEL`（可选，默认 `kimi-k2-turbo-preview`）

注：也可通过 `configs/model.json` 调整 base_url/模型与温度/是否流式。

## 配置要点（configs）

- `characters.json`：决定参与者顺序与类型
  - `participants`: 出场顺序（如 ["Amiya","Doctor", ...]）
  - 角色项：`type: "player"|"npc"`，`persona`（人设），`dnd`（AC/HP/能力/熟练），`position`（起始坐标）
- `prompts.json`（可选）：玩家人设、名称映射、NPC/敌人提示词模板（示例见 `prompts.json.example`）
- `model.json`：`base_url`、`npc` 模型名、温度、是否流式
- `time_rules.json`：各意图的时间消耗（分钟）
- `relation_rules.json`：默认关系变更策略
- `narration_policy.json`：旁白生成策略（长度/候选/焦点循环等）
- `narration_env.json`：按场景配置视觉/声响/空气/道具关键词
- `feature_flags.json`：如 `log_narrator_debug`（将旁白调试写入 run.log）

## 世界工具（节选）

- 时间与事件：`advance_time(mins)`, `schedule_event(name, at_min, ...)`（自动触发）
- 关系与物品：`change_relation(a,b,delta,reason)`, `grant_item(target,item,n)`
- 角色：`set_dnd_character(...)`, `get_stat_block(name)`, `damage(name,n)`, `heal(name,n)`
- 检定/攻击（D&D风格）：`skill_check_dnd(name, skill, dc, advantage?)`, `attack_roll_dnd(attacker, defender, ...)`
- 环境/气氛：`describe_world(detail?)`, `adjust_tension(delta)`, `add_mark(text)`
- 目标：`add_objective(name)`, `complete_objective(name, note?)`, `block_objective(name, reason?)`

工具返回 `ToolResponse`；本版本不再自动裁决（仅展示 NPC 对白与意图）。

## 开发与规范

- 代码布局采用 src/ 包结构；`pyproject.toml` 提供脚本入口与开发工具。
- 统一格式与检查：`ruff`、`mypy`（见 `.pre-commit-config.yaml`）。可启用 pre-commit：`pre-commit install`。
- 最小单测样例见 `tests/`，包含世界工具的健壮性校验。

## 常见问题

- `ModuleNotFoundError: agentscope`：确认已在 `npc-talk` 环境中，并已按 `environment.yml` 安装。
- Kimi 报错/无响应：检查 `MOONSHOT_API_KEY`、网络连通、`KIMI_BASE_URL` 与模型名。
- 中文输出不稳定/重复：可在 `configs/narration_policy.json` 调整策略或关闭 `no_filter/raw_pick/disable_fallback` 等策略开关。

## 后续可做
- 扩展剧情 JSON（docs/plot.story.json），若未来恢复导演逻辑可用于触发事件
- 引入结构化输出校验/重试与可视化
- 接入游戏引擎（HTTP/MCP 工具）
