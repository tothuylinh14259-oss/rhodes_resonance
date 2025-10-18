# CoC 百分制数值与 Terra 适配方案

目标
- 用 CoC（d100）替换/并存现有 D&D 六围系统；保持 JSON 兼容与逐步迁移。
- 引入明日方舟世界要素：源石技艺、感染（Oripathy）轨道、物理/术式护甲、职业包。
- 让现有引擎可按 `system` 切换（`dnd` 与 `coc` 并存，便于 A/B 测试）。

数据结构（JSON）
- 在角色对象上新增：`system: "coc"` 与 `coc` 块；保留 `dnd` 以兼容旧逻辑。
- `coc` 建议结构：
```json
{
  "characteristics": {
    "STR": 60, "CON": 70, "DEX": 80, "INT": 50, "POW": 70,
    "APP": 50, "EDU": 60, "SIZ": 55, "LUCK": 50
  },
  "derived": {
    "hp": 13, "san": 70, "mp": 14, "move": 8, "build": 0, "db": "0"
  },
  "terra": {
    "class": "Supporter",
    "race": "Cautus",
    "faction": "Rhodes Island",
    "arts": { "affinity": 30, "school": "N/A", "resist": 30 },
    "infection": { "stage": 0, "stress": 0, "crystal_density": 0 },
    "protection": { "physical_armor": 0, "arts_barrier": 0 }
  },
  "skills": {
    "Tactics": 70,
    "Command": 65,
    "FirstAid": 40,
    "Medicine": 20,
    "Stealth": 40,
    "Perception": 50,
    "RangedWeapons": 35,
    "MeleeWeapons": 30,
    "Dodge": 40,
    "Negotiation": 50,
    "OriginiumTech": 30,
    "Lore_Terra": 40
  },
  "talents": ["指挥协调", "冷静决断"],
  "inventory": { "doctor_knife": 1 }
}
```

角色生成与迁移
- 六围换算（从 D&D 估算到 CoC，便于一次性填充初稿）：`CoC = round(DND × 5)`。
  - D&D: STR/DEX/CON/INT/WIS/CHA → CoC: STR/DEX/CON/INT/POW/APP；补充 SIZ≈40–60（体型），EDU≈50–70，LUCK≈40–60。
- 派生：`hp = (CON + SIZ) / 10`；`san = POW`；`mp = POW / 5`；`move` 依据 DEX/SIZ 与职业 6–9；`build/db` 按 CoC7e 体格表。
- AC → 护甲：用 `protection.physical_armor`（0 轻便，1–2 战术，3–4 重装）；术式用 `arts_barrier`。
- 迁移策略：为每个角色先添加 `system: "coc"` 与 `coc`，暂不移除 `dnd`；引擎按 `system` 分支读取。

技能清单与职业包（示例）
- 基础技能：
  - 战术类：`Tactics`，`Command`
  - 侦察与行动：`Perception`，`Stealth`，`Athletics/Climb`（可选），`Dodge`
  - 战斗：`MeleeWeapons`，`RangedWeapons`
  - 医疗：`FirstAid`，`Medicine`
  - 知识与工学：`OriginiumTech`，`Lore_Terra`，`Engineering`（可选）
  - 交互：`Negotiation`，`Psychology`
  - 术式：`Arts_<School>`（如 `Arts_Spirit`），`Concentration`
- 职业包（示意，使用 CoC7e 分配法：职业点=EDU×4，兴趣点=INT×2。也可采用“EDU×2+关键×2”的简化法）：
  - Vanguard 先锋：Athletics，Tactics，Perception，RangedWeapons，MeleeWeapons，Dodge，Negotiation
  - Guard 近卫：MeleeWeapons，Dodge，Athletics，Perception，Tactics，Psychology，FirstAid
  - Defender 重装：MeleeWeapons，Tactics，Engineering/OriginiumTech，FirstAid，Perception，Dodge
  - Sniper 狙击：RangedWeapons，Stealth，Perception，Tactics，Dodge，Engineering（器材）
  - Caster 术师：Arts_<School>，Concentration，OriginiumTech，Lore_Terra，Dodge，Perception，Psychology
  - Medic 医疗：Medicine，FirstAid，Psychology，Negotiation，Perception，OriginiumTech
  - Supporter 辅助：Negotiation，Psychology，Tactics，Perception，Ranged/Melee（二选一），Concentration
  - Specialist 特种：Stealth，Athletics，Engineering/OriginiumTech，Perception，Dodge，Tactics

检定与对抗
- 标准 d100：成功≤目标；困难=目标/2；极难=目标/5。
- 对抗：进攻检定 vs 防御/抗性（更高等级的成功胜出：极难>困难>常规；同等级取更低点数胜）。
- 术式：`Arts_<School>` 命中/效应 vs `arts.resist` 或 `POW`（依学派）；心智冲击可对 `SAN/POW` 对抗。

伤害与护甲
- 物理伤害：由武器决定；`protection.physical_armor` 作为固定减伤或分档（建议 0–4）。
- 术式伤害：可被 `arts_barrier` 减免；部分术式对护甲有穿透或溢出到 SAN（由武器/法术定义）。

感染轨道（Oripathy Stress Track）
- 字段：`terra.infection = { stage: 0–3, stress: 0–100, crystal_density: 0–5 }`
  - stage：0 未感染；1 早期；2 中期；3 晚期（决定下限与惩罚）。
  - stress：短期波动条；达到阈值会“发作”。
  - crystal_density：长期积累；阶段上升时 +1，带来持久影响。
- 阈值与下限：阈值 20/50/80；各阶段下限为 0/20/50/80（普通治疗不会把 stress 降到下限以下）。
- 获得应激（例）：
  - 源石暴露：轻 1d4，中 1d6+1，重 2d6，灾害 2d10。
  - 重伤/开放性伤口：1d4（已妥善包扎则不触发）。
  - 过载施术：当轮或当场景超阈施术时 1d4（术师可更高；或按施术失败时触发）。
  - 高压连战/奔袭：场景末尾 1d3（后勤/休整可抵消）。
  - 医疗失败/无防护操作污染物：1d3～1d6。
- 暴露对抗：是否吃满这次应激
  - 目标 = `max(arts.resist, round((CON+POW)/2)) + 装备加值 + 医疗/去污加值 − 阶段惩罚`
    - 装备：标准防护 +10，重防护 +20；阶段惩罚：0/−10/−20/−30；去污/医学成功 +10。
  - 掷 1d100：成功→本次应激减半；极难成功→免疫；大失败→本次应激×1.5。
- 发作效果：
  - 20 轻度：剧痛/咳血 1d10 分钟；物理/施术检定 −10；SAN 0/1。
  - 50 中度：检定 −20；每轮需 CON 困难检定，失败行动受限；SAN 0/1d2；继续施术额外 +1d4 应激。
  - 80 重度：立刻 CON 极难检定，失败受 1d6 HP（术式护盾可减半，物理护甲无效）；强制 POW 检定，失败眩晕 1d3 轮；SAN 1/1d4。
- 阶段进展（长期恶化）：
  - `stress > 100` 或一周内两次重度发作 → `stage+1`，`crystal_density+1`，`stress` 置为新阶段下限。
  - 每升 1 阶，长期影响二选一（由 GM/剧情裁定）：
    - 体能衰退：CON −5 或 `arts.resist −10`
    - 术式亲和上升但难控：`arts.affinity +5`，且过载施术应激骰升一档
  - 阶段通常不可逆；高端医疗可“稳定”但不回退阶段。
- 恢复与干预：
  - 休整：每天 −1d3（不低于阶段下限）。
  - 医疗（Medicine）：每周 −1d6；高级疗程 −2d6（可能副作用）。
  - 抑制剂：立即 −1d6，应激；副作用可能使 DEX/CON 检定 −10 持续到场景结束。
  - 去污/防护：当次暴露前置 +10～+20 抗性。
- 与其他数值联动：
  - 护甲：`physical_armor` 不减免发作伤害；`arts_barrier` 可对重度发作伤害生效（减半或按数值减伤）。
  - 羁绊：队友对你进行去污/包扎/搬运时，`relations > 60` 给予相关检定 +10。
  - 技能：`FirstAid` 可立刻取消“伤口暴露”类应激一次；`OriginiumTech` 可在灾害中将暴露降一档。

与代码集成建议
- 解析：在读取角色时检查 `system` 字段，优先走 `coc` 分支；保留 `dnd` 逻辑不动。
- 检定函数：新增 `skill_check_coc(name, skill, difficulty)` 与 `opposed_check_coc(a, b)`；术式走 `arts_vs_resist(attacker, defender, school)`。
- 感染流程：`apply_exposure(actor, level, source)` → 对抗 → 增加 `infection.stress` → 处理阈值与发作 → 可能调用 `advance_infection_stage`。
- 伤害结算：物理先减 `physical_armor`；术式按 `arts_barrier`；必要时再施加 SAN 溢出。

示例片段（Amiya 术师，数值示意）
```json
{
  "Amiya": {
    "type": "npc",
    "system": "coc",
    "coc": {
      "characteristics": {
        "STR": 40, "CON": 60, "DEX": 65, "INT": 60, "POW": 80,
        "APP": 60, "EDU": 55, "SIZ": 45, "LUCK": 55
      },
      "derived": { "hp": 11, "san": 80, "mp": 16, "move": 8, "build": -1, "db": "-1d4" },
      "terra": {
        "class": "Caster",
        "race": "Cautus/Chimera",
        "faction": "Rhodes Island",
        "arts": { "affinity": 80, "school": "Spirit/Binding", "resist": 50 },
        "infection": { "stage": 2, "stress": 35, "crystal_density": 2 },
        "protection": { "physical_armor": 0, "arts_barrier": 1 }
      },
      "skills": {
        "Arts_Spirit": 80,
        "Concentration": 60,
        "Tactics": 50,
        "Perception": 55,
        "Dodge": 40,
        "FirstAid": 35,
        "Psychology": 45,
        "Negotiation": 50,
        "Lore_Originium": 60
      },
      "talents": ["心灵束缚", "激励"],
      "inventory": { "amiya_focus": 1 }
    }
  }
}
```

示例片段（Doctor 指挥，数值示意）
```json
{
  "Doctor": {
    "type": "player",
    "system": "coc",
    "coc": {
      "characteristics": {
        "STR": 60, "CON": 70, "DEX": 80, "INT": 50, "POW": 70,
        "APP": 50, "EDU": 60, "SIZ": 55, "LUCK": 50
      },
      "derived": { "hp": 13, "san": 70, "mp": 14, "move": 8, "build": 0, "db": "0" },
      "terra": {
        "class": "Supporter",
        "race": "Cautus",
        "faction": "Rhodes Island",
        "arts": { "affinity": 30, "school": "N/A", "resist": 30 },
        "infection": { "stage": 0, "stress": 0, "crystal_density": 0 },
        "protection": { "physical_armor": 0, "arts_barrier": 0 }
      },
      "skills": {
        "Tactics": 70,
        "Command": 65,
        "FirstAid": 40,
        "Medicine": 20,
        "Stealth": 40,
        "Perception": 50,
        "RangedWeapons": 35,
        "MeleeWeapons": 30,
        "Dodge": 40,
        "Negotiation": 50,
        "OriginiumTech": 30,
        "Lore_Terra": 40
      },
      "talents": ["指挥协调", "冷静决断"],
      "inventory": { "doctor_knife": 1 }
    }
  }
}
```

落地建议
- 先为 `configs/characters.json` 的 4 人补 `system/coc` 区块；跑小规模测试观察手感。
- 在 `world/tools.py` 增加 CoC 检定与感染流程函数（不影响 D&D）。
- 确认职业包与技能清单，再批量完善其余角色。
- 若需要，我可写一个迁移脚本，从现有 D&D 数值自动生成 CoC 初稿（人工复核后入库）。

## 感染通道（Channel Catalog）

目的
- 将“如何被感染/受污染”具体化为可判定的渠道与因子，便于在场景里快速选定暴露等级与对抗加值。
- 通道 = 传播介质 + 接触方式 + 时长/强度；再叠加防护/去污修正，最终映射到应激骰（轻/中/重/灾害）。

通道清单（建议）
- 空气粉尘/气溶胶：矿区粉尘、战场扬尘、术式余渣。
  - 轻：开放空间短时暴露（1d4）；中：密闭/半密闭或近距离尘源（1d6+1）；重：沙暴/坍塌扬尘中心（2d6）；灾害：大范围术爆/工厂泄露（2d10）。
  - 防护加值：口罩+5，滤毒半面+10，正压面罩/全面罩+15，密闭防化服+20；机械通风/喷淋抑尘+5～+10。
- 飞沫/体液溅射：近距离谈话、救护、搏斗时的血液/唾液飞溅。
  - 基值：1d4；进入眼口鼻视同中等（1d6）；与开放伤口叠加则按“开放伤口”处理。
  - 防护加值：护目镜+5，面罩+10，防渗手套+5。
- 皮肤接触（完整皮肤）：短时接触污染表面/粉尘沉降。
  - 基值：1；立即去污（<1 分钟）则减半；持续接触或高温高湿环境+1 级。
  - 防护加值：手套+5，防护服+5，及时去污+10。
- 皮肤接触（开放伤口/擦破）：污染物进入伤口、战斗撕裂处暴露。
  - 轻伤：1d6；重度污染或渗入时间长→2d6；异物留置（碎屑/结晶）额外判一次应激（1d4）。
  - 防护：即时包扎与封闭敷料可将本次暴露降 1 级；FirstAid 成功可直接取消一次“伤口暴露”。
- 摄入/饮水污染：误食/误饮被源石粉尘或加工副产物污染的食物与水。
  - 少量摄入：1d6+1；可疑来源持续摄入：2d6；高度污染源：2d10。
  - 防护：净化/煮沸/过滤成功 +10；碘/专用净化剂按剧情裁定 +5～+10。
- 医疗/实验暴露：针扎、破损手套操作、样本泄漏、离心/喷溅事故。
  - 表面接触：1d4；针扎/切割：1d6+1；高浓度样本气溶胶：2d6。
  - 防护：规范操作+5，生物安全柜/屏障+10，双层手套+5，面屏+5。
- 术式回流/过载反馈（施术者专项）：施术失败、超阈维持、反制被破。
  - 轻度过载：1d4；失败大偏差/被反制：1d6+1；灾变术式余震：2d6。
  - 对抗：以 `arts.resist` 或 `POW` 为主；`arts_barrier` 可使本次应激减半。
- 爆炸/坍塌碎屑：含结晶碎片/粉尘的爆炸冲击、建筑坍塌。
  - 基值：2d6；若产生大量粉尘云，额外判一次“空气粉尘”。
  - 防护：头盔与护甲对碎片 HP 伤害有效，但对感染应激仅小幅 +5；呼吸防护按“空气粉尘”。
- 环境重度污染（矿区/工厂/船舱）：在污染区域停留与高强度活动。
  - 每 10 分钟：1d3；高强度运动/战斗：升至 1d6；若同时有粉尘源，叠加“空气粉尘”。
  - 防护：区域去污/通风+5～+10；定时更换滤芯+5。
- 生物载体（源石虫/携带者）：被咬/蜇、接触其体液。
  - 咬/蜇：1d6 并可能附带 HP 伤害；体液接触：1d4。
  - 防护：厚衣/护甲对咬合有效（按护甲减免 HP），感染应激对抗 +5。

快速量化规则（可选）
- 暴露评分 = 距离因子 + 时长因子 + 量级因子 + 伤口因子 − 防护因子；
  - 距离：贴身+2，1 米内+1，5 米 0，10 米 −1；
  - 时长：<1 分钟 0，1–10 分钟 +1，10–60 分钟 +2，>60 分钟 +3；
  - 量级：可见粉尘/体液 +1，扬尘浓云 +2，喷溅/针扎 +2，直接摄入/碎片嵌入 +3；
  - 伤口：完整 0，小伤口 +1，开放伤口 +2；
  - 防护：口罩 +5，半面 +10，全面/正压 +15，防化服 +5，双层手套 +5，规范操作/去污 +5～+10。
- 评分映射到应激骰：≤1→1；2–3→1d4；4–5→1d6（或 1d6+1）；6–7→2d6；≥8→2d10。

去污流程（建议加值）
- 立即机械去污（拂去/刷洗/冲淋）在 1 分钟内：本次暴露应激减半，且暴露评分 −2。
- 化学去污（净化液/消毒）：再 −1；若对皮肤刺激强，可能造成 DEX 检定 −10 的临时副作用（由 GM 裁定）。
- 脱卸污染装备并隔离：后续场景的“空气粉尘”类暴露下降 1 档。

场景模板（裁定范例）
- 矿区塌方扬尘：空气粉尘（重 2d6）+ 碎屑（2d6）；若 10 分钟内无法撤离，再追加环境重度污染（每 10 分钟 1d3）。
- 近距离搏斗溅血：飞沫/体液（1d4）；若己方有开放伤口，追加开放伤口（1d6）。
- 术式反制失败：术式回流（1d6+1）；若击碎含结晶障壁，同时判空气粉尘（中 1d6+1）。
- 医疗针扎事故：医疗暴露（1d6+1）；若立即去污与急救成功，可降至 1d4。

规则衔接
- 以上通道最终都走“暴露对抗”流程：确定应激骰 → 投对抗（`arts.resist`/`(CON+POW)/2` + 装备/去污加值 − 阶段惩罚）→ 结算 `infection.stress` 变化与阈值发作。
- 若一次事件涉及多通道（如爆炸+扬尘），分别结算但 GM 可合并为“主通道骰 + 次通道一档下调”的单次处理以加快节奏。

MP
  在我们 Terra 适配里的作用（建议）

  - 把 MP 视作“源石技艺能量池”。凡 damage_type: "arts" 的攻击或术式类能力，从 MP 扣费；物理不扣。
  - 简单计费（好落地）：小术式 1 MP；常规 2 MP；强术 3–5 MP；持续类每轮再付 1 MP。
  - 0 MP 时的处置（二选一，等你拍板）：
      1. 禁止施术（最稳）；或
      2. 允许“过载”施术但本次掷骰 −20，并立刻+1d4 感染应激（走感染轨道）。
  - 互动：可允许消耗 MP 触发一次性“术式护盾”加值（如 1 MP 临时 +1 护甲，仅对术伤；可先不开）。