# NPC Talk Demo (Agentscope)

NPC 群聊 + KP 审判 + 微旁白的最小可跑通 Demo。严格依赖真实 Agentscope（使用 `agentscope.pipeline`），并通过 Kimi 的 OpenAI 兼容接口驱动 LLM NPC。

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
python src/main.py
```

运行期望：玩家与两个 NPC 在“罗德岛·会议室”进行回合制对话；KP 负责改写/确认玩家意图并进行规则裁决（时间推进、检定/攻击、关系/物品变更等），必要时按剧情/策略插入事件或敌对单位；旁白在关键动作后生成一段中文微叙事。过程写入 `run.log`。

## 目录结构

```
npc_talk_demo/
  src/
    main.py                 # 入口：回合驱动、KP 审判、事件插入、日志
    agents/
      player.py             # Player（人类输入，支持 /quit /skip）
      kp.py                 # KP（改写/澄清/确认玩家意图 + 工具裁决 + 导演动作）
      npc.py                # SimpleNPCAgent（保留的最简基类实现，当前未启用）
      narrator.py           # 旁白（LLM 生成微叙事）
    world/
      tools.py              # 世界状态与工具（时间、关系、物品、D&D检定/攻击、事件时钟…）
  configs/
    characters.json         # 参与者/人设/D&D数值/出场顺序
    model.json              # LLM 接入（base_url、kp/npc 模型等）
    prompts.json            # 玩家人设、导演策略、NPC/敌人提示词模板、名称映射
    narration_policy.json   # 旁白策略（长度/候选/焦点轮换…）
    narration_env.json      # 场景关键词（visual/sound/air/props）
    time_rules.json         # 意图用时规则
    relation_rules.json     # 关系变更规则
    feature_flags.json      # 特性开关（如旁白调试日志）
  docs/
    spec.md                 # 项目规范（设计/接口/约定）
    plot.story.json         # 可选：剧情节拍（KP 导演可参考）
  environment.yml           # Conda 环境（Python 3.11 + Agentscope）
  run.log                   # 运行日志（每次覆盖）
```

## 运行时交互

- 玩家输入：直接在控制台输入对白/行动
- 支持指令：
  - `/quit`：结束本次冒险（KP 会广播一个收尾）
  - `/skip`：本回合“被动姿态/微动作/轻观察”，不主动推进
- 每回合流程（简化）：
  1) 主持信息+世界概要
  2) Player↔KP 握手（KP 仅与玩家私聊澄清/改写，确认后再广播最终玩家消息）
  3) KP 立即裁决并广播结果（可能触发时间推进、检定/攻击、事件等）
  4) KP 视剧情/策略插入广播/敌人/目标等导演动作
  5) NPC 依序行动，每个动作后立即裁决与广播
  6) 旁白根据动作余波生成一段中文微叙事（可通过 feature_flags 控制）

## 必要环境变量

- `MOONSHOT_API_KEY`（必填）：Kimi API Key
- `KIMI_BASE_URL`（可选，默认 `https://api.moonshot.cn/v1`）
- `KIMI_MODEL`（可选，默认 `kimi-k2-turbo-preview`）

注：也可通过 `configs/model.json` 调整 base_url/模型与温度/是否流式。

## 配置要点（configs）

- `characters.json`：决定参与者顺序与类型
  - `participants`: 出场顺序（如 ["Amiya","Kaltsit","Doctor"]）
  - 角色项：`type: "player"|"npc"`，`persona`（人设），`cli_prompt`（玩家输入提示），`dnd`（AC/HP/能力/熟练）
- `prompts.json`：玩家人设、导演策略、名称映射、NPC/敌人提示词模板
- `model.json`：`base_url`、`kp`/`npc` 模型名、温度、是否流式
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

工具返回 `ToolResponse`，KP 在裁决后广播文本块并更新世界状态。

## 日志与调试

- `run.log`：每条广播消息与（可选）旁白调试信息
- 观察世界快照：控制台会打印 `[system] world: {...}`

## 常见问题

- `ModuleNotFoundError: agentscope`：确认已在 `npc-talk` 环境中，并已按 `environment.yml` 安装。
- Kimi 报错/无响应：检查 `MOONSHOT_API_KEY`、网络连通、`KIMI_BASE_URL` 与模型名。
- 中文输出不稳定/重复：可在 `configs/narration_policy.json` 调整策略或关闭 `no_filter/raw_pick/disable_fallback` 等策略开关。

## 后续可做
- 扩展剧情 JSON（docs/plot.story.json）与 KP 导演策略
- 引入结构化输出校验/重试与可视化
- 接入游戏引擎（HTTP/MCP 工具）
