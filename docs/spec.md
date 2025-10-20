# 项目规范（NPC 群聊 Demo）

目标与约束
- 子部件之间不互相依赖：world / actions / agents 通过依赖注入解耦；main 负责编排。
- 功能分离、好理解：world 管状态与规则；actions 是工具外观；agents 专注于提示词与调用工具。
- 以数据为起点：参与者与坐标从 story 派生；人物数值/物品/武器从 characters/ weapons 提供。
- agent 操作世界以运行：通过 CALL_TOOL 直接调用 actions，actions 委托 world 完成状态变更。

Prompt 组合
- 指导 prompt（Agent sys_prompt）：包含人设/外观/口癖、关系提示、武器清单与工具规则。
- 世界概要（Host）：根据 `WORLD.snapshot()` 渲染，高频率在每个 NPC 行动前广播一次。
- 行动记忆：取“最近播报”（包含工具结果转播），供下一位 NPC 决策参考。

流程概述（无 KP）
1) 载入配置：prompts/model/story/characters/weapons（缺失的 prompts 将使用默认模板）。
2) 由 story 的初始坐标推导参与者与顺序；若无参与者，则在进入 Hub 前直接结束（本次更新）。
3) 初始化角色卡与物品、武器表；根据 `characters.json` 的 `inventory` 给予角色初始武器。
4) 进入回合循环：
   - 每名 NPC 回合前：广播世界概要与最近播报；
   - NPC 输出对白与 CALL_TOOL；派发工具并广播结果；
   - 若场上无敌对则退出战斗并结束；否则进入下一回合。

世界模型与关键规则（world/tools.py）
- 时间/天气/地点/目标/细节、坐标、关系、人物卡（含 persona/appearance/quotes）、物品、武器表、战斗轮转、守护关系。
- 武器攻击：`attack_with_weapon(attacker, defender, weapon, advantage?)`
  - 触及范围与伤害表达式从 `weapons.json` 获取；必须“持有”该武器；不会自动靠近；距离不足直接失败。
  - 守护：`set_protection(guardian, protectee)`，拦截条件为相邻（≤1步）且 guardian 有可用反应，且在攻击者触及范围内。
- 检定：`skill_check_dnd(name, skill, dc, advantage)`；豁免、对抗等参见实现。

工具外观（actions/npc.py）
- perform_attack(attacker, defender, weapon, reason)
- advance_position(name, target:[x,y], reason)  // 自动使用剩余移动力；target 必须为 [x,y]
- adjust_relation(a, b, value, reason)
- transfer_item(target, item, n, reason)
- set_protection(guardian, protectee, reason)
- clear_protection(guardian?, protectee?, reason)

错误可观测性（本次更新）
- 世界概要广播失败：记录 `error(context_world_render)` 事件但不中断。
- 场景细节写入失败：记录 `error(scene_details_append)`。
- 武器表载入失败：记录 `error(weapon_defs_load)`。

目录结构（当前）
```
src/
  main.py
  actions/
    npc.py
  agents/
    factory.py
  world/
    tools.py
  eventlog/
    *.py
  settings/
    loader.py
configs/
  characters.json
  story.json
  model.json
  weapons.json
logs/
  run_events.jsonl
  run_story.log
  prompts/
    <actor>_prompt.txt  # 系统 Prompt + 注入内存的调试转储；仅保留最新，运行开始清空历史

注：不再生成 `*_context_dev.log`（开发态 context 卡片）；统一以 `run_events.jsonl` 与 `run_story.log` 作为审计主源。若开启 `DEBUG_DUMP_PROMPTS`，则在 `logs/prompts/` 中仅保留每个角色最新一次的 Prompt 转储。
```
