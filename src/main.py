#!/usr/bin/env python3
"""
Minimal NPC group chat + story driving demo (strict Agentscope version).
- Requires real `agentscope` with `agentscope.pipeline` present (no fallback).
- Demonstrates: MsgHub, sequential speaking, dynamic join, simple world tools.
Run (after conda env activation): python src/main.py
"""
from __future__ import annotations
import asyncio
import sys
import os

# Strict import: must use real agentscope.
from agentscope.pipeline import MsgHub, sequential_pipeline  # type: ignore
from agentscope.message import Msg  # type: ignore
from agentscope.agent import ReActAgent  # type: ignore
from agentscope.model import OpenAIChatModel  # type: ignore
from agentscope.formatter import OpenAIChatFormatter  # type: ignore
from agentscope.memory import InMemoryMemory  # type: ignore
from agentscope.tool import Toolkit  # type: ignore

from agents.player import PlayerAgent
from agents.kp import KPAgent
from agentscope.agent import AgentBase  # type: ignore
from agents.narrator import Narrator

# --- Configurable prompts & personas (overridable via configs/prompts.json) ---
_DEFAULT_PLAYER_PERSONA = (
    "角色：罗德岛‘博士’，战术协调与决策核心。\n"
    "背景：在凯尔希与阿米娅的协助下进行战略研判，偏好以信息整合与资源调配达成目标。\n"
    "说话风格：简短、理性、任务导向；避免夸饰与情绪化表达。\n"
    "边界：不自称超自然或超现实身份；不越权知晓未公开的机密情报。\n"
)
_PROMPTS_CFG: dict = {}
_NPC_PROMPT_TEMPLATE: str | None = None
_ENEMY_PROMPT_TEMPLATE: str | None = None
_NAME_MAP_CFG: dict = {}
_MODEL_CFG: dict = {}
_NARR_POLICY: dict = {}
_FEATURE_FLAGS: dict = {}
from world.tools import (
    WORLD,
    advance_time,
    change_relation,
    grant_item,
    describe_world,
    set_scene,
    add_objective,
    set_character,
    get_character,
    damage,
    heal,
    roll_dice,
    skill_check,
    # D&D-like
    set_dnd_character,
    get_stat_block,
    skill_check_dnd,
    saving_throw_dnd,
    attack_roll_dnd,
    get_turn,
    # Director/event helpers
    schedule_event,
    complete_objective,
    block_objective,
)
import json as _json


def banner():
    print("=" * 60)
    print("NPC Talk Demo (Agentscope) [real agentscope]")
    print("=" * 60)


# ---- Kimi (Moonshot) integration helpers ----
def make_kimi_npc(name: str, persona: str, prompt_template: str | None = None, allowed_names: str | None = None) -> ReActAgent:
    """Create an LLM-backed NPC using Kimi's OpenAI-compatible API.

    Required env vars (set in your conda env):
    - MOONSHOT_API_KEY: your Kimi API key
    - KIMI_BASE_URL: e.g. https://api.moonshot.cn/v1
    - KIMI_MODEL: e.g. kimi-k2-turbo-preview or moonshot-v1-128k
    """
    api_key = os.environ["MOONSHOT_API_KEY"]
    base_url = _MODEL_CFG.get("base_url") or os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    sec = _MODEL_CFG.get("npc") or _MODEL_CFG
    model_name = sec.get("model") or os.getenv("KIMI_MODEL", "kimi-k2-turbo-preview")

    # Build system prompt from template or fallback
    tools = "describe_world()"
    intent_schema = (
        "{\n  \"intent\": \"attack|talk|investigate|move|assist|use_item|skill_check|wait\",\n"
        "  \"target\": \"目标名称\",\n  \"skill\": \"perception|medicine|...\",\n"
        "  \"ability\": \"STR|DEX|CON|INT|WIS|CHA\",\n  \"proficient\": true,\n  \"dc_hint\": 12,\n"
        "  \"damage_expr\": \"1d4+STR\",\n  \"time_cost\": 1,\n  \"notes\": \"一句话说明意图\"\n}"
    )
    sys_prompt = None
    tpl = prompt_template or _NPC_PROMPT_TEMPLATE
    if tpl:
        try:
            sys_prompt = tpl.format(name=name, persona=persona, tools=tools, intent_schema=intent_schema, allowed_names=(allowed_names or "Doctor, Amiya"))
        except Exception:
            sys_prompt = None
    if not sys_prompt:
        # Fallback built-in prompt (avoid format() so JSON braces are literal)
        header = f"你是游戏中的NPC：{name}。人设：{persona}。\n"
        rules = (
            "对话要求：\n"
            "- 先用简短中文说1-2句对白/想法/微动作，符合人设。\n"
            "- 然后给出一个 JSON 意图（不要调用任何工具；裁决由KP执行）。\n"
            "- 若需要了解环境信息，可调用 describe_world()；除此之外不要调用其他工具。\n"
            f"- 参与者名称（仅可用）：{allowed_names or 'Doctor, Amiya'}\n"
            "- 意图JSON的 target 字段必须从上述列表中选择；禁止使用别称/编号（如 Player/玩家/Player1）。\n"
            "意图JSON格式（仅保留需要的字段）：\n"
        )
        example = (
            "输出示例：\n"
            "阿米娅看向博士，压低声音：‘要先确认辐射数据。’\n"
            "```json\n"
            '{"intent":"skill_check","target":"Amiya","skill":"investigation","dc_hint":12,"notes":"核对监测表"}'
            "\n```\n"
        )
        sys_prompt = header + rules + intent_schema + "\n" + example

    model = OpenAIChatModel

    model = OpenAIChatModel(
        model_name=model_name,
        api_key=api_key,
        stream=bool(sec.get("stream", True)),
        client_args={"base_url": base_url},
        generate_kwargs={"temperature": float(sec.get("temperature", 0.7))},
    )

    # Equip only describe_world; all other tools are executed by KP adjudicator.
    toolkit = Toolkit()
    toolkit.register_tool_function(describe_world)

    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=model,
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
        toolkit=toolkit,
    )


async def tavern_scene():
    # Load external prompts (optional)
    prompts_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs", "prompts.json")
    player_persona = _DEFAULT_PLAYER_PERSONA
    try:
        with open(prompts_path, "r", encoding="utf-8") as f:
            _PROMPTS_CFG.update(_json.load(f) or {})
        player_persona = _PROMPTS_CFG.get("player_persona") or player_persona
        # templates
        global _NPC_PROMPT_TEMPLATE, _ENEMY_PROMPT_TEMPLATE, _NAME_MAP_CFG
        _NPC_PROMPT_TEMPLATE = _PROMPTS_CFG.get("npc_prompt_template")
        _ENEMY_PROMPT_TEMPLATE = _PROMPTS_CFG.get("enemy_prompt_template")
        _NAME_MAP_CFG = _PROMPTS_CFG.get("name_map") or {}
    except Exception:
        pass

    # Load model config (optional)
    model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs", "model.json")
    try:
        with open(model_path, "r", encoding="utf-8") as f:
            _MODEL_CFG.update(_json.load(f) or {})
    except Exception:
        pass
    # Load narration policy & env keywords (optional)
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs", "narration_policy.json"), "r", encoding="utf-8") as f:
            _NARR_POLICY.update(_json.load(f) or {})
    except Exception:
        pass
    # Load feature flags (optional)
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs", "feature_flags.json"), "r", encoding="utf-8") as f:
            _FEATURE_FLAGS.update(_json.load(f) or {})
    except Exception:
        pass

    # Build actors from characters.json or fallback
    chars_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs", "characters.json")
    npcs_list: list[ReActAgent] = []
    player: PlayerAgent | None = None
    participants_order: list[AgentBase] = []
    if os.path.exists(chars_path):
        try:
            with open(chars_path, "r", encoding="utf-8") as f:
                char_cfg = _json.load(f) or {}
        except Exception:
            char_cfg = {}
        # Initialize D&D blocks and agents according to config
        order = char_cfg.get("participants") or []
        allowed_names_str = ", ".join(order) if isinstance(order, list) else "Doctor, Amiya"
        for name in order:
            entry = (char_cfg.get(name) or {}) if isinstance(char_cfg, dict) else {}
            typ = str(entry.get("type") or "npc")
            # Stat block
            dnd = entry.get("dnd") or {}
            if dnd:
                try:
                    set_dnd_character(
                        name=name,
                        level=int(dnd.get("level", 1)),
                        ac=int(dnd.get("ac", 10)),
                        abilities=dnd.get("abilities") or {},
                        max_hp=int(dnd.get("max_hp", 8)),
                        proficient_skills=dnd.get("proficient_skills") or [],
                        proficient_saves=dnd.get("proficient_saves") or [],
                    )
                except Exception:
                    pass
            if typ == "player":
                cli_prompt = entry.get("cli_prompt") or "你> "
                player = PlayerAgent(name=name, prompt=cli_prompt)
                participants_order.append(player)
            else:
                persona = entry.get("persona") or "一个简短的人设描述"
                agent = make_kimi_npc(name, persona, prompt_template=_NPC_PROMPT_TEMPLATE, allowed_names=allowed_names_str)
                npcs_list.append(agent)
                participants_order.append(agent)
        # Safety net: if config existed but failed to create a player, fallback to default Player
        if player is None:
            player = PlayerAgent(name="Doctor", prompt="博士> ")
            participants_order.append(player)
    else:
        # Fallback to hardcoded actors
        allowed_names_str = ", ".join(["Kaltsit", "Amiya", "Doctor"])  # fallback visible names
        amiya = make_kimi_npc("Amiya", "罗德岛公开领导人阿米娅。温柔而坚定，理性克制，关切同伴；擅长源石技艺（术师），发言简洁不夸张。", prompt_template=_NPC_PROMPT_TEMPLATE, allowed_names=allowed_names_str)
        kaltsit = make_kimi_npc("Kaltsit", "罗德岛医疗部门负责人凯尔希。冷静苛刻、直言不讳，注重风险控制与证据；医疗/生物技术专家。", prompt_template=_NPC_PROMPT_TEMPLATE, allowed_names=allowed_names_str)
        player = PlayerAgent(name="Doctor", prompt="博士> ")
        npcs_list = [kaltsit, amiya]
        participants_order = [kaltsit, amiya, player]

    # KP (GM)
    kp = KPAgent(name="KP", player_persona=player_persona, player_name=getattr(player, "name", "Doctor"))
    kp.set_world_snapshot_provider(lambda: WORLD.snapshot())
    # Apply optional prompt overrides to KP
    try:
        if _PROMPTS_CFG.get("kp_system_prompt"):
            kp.set_kp_system_prompt(_PROMPTS_CFG.get("kp_system_prompt"))
    except Exception:
        pass
    try:
        if _PROMPTS_CFG.get("director_policy"):
            kp.set_director_policy(_PROMPTS_CFG.get("director_policy"))
    except Exception:
        pass
    try:
        if _NAME_MAP_CFG:
            kp.set_name_map(_NAME_MAP_CFG)
    except Exception:
        pass
    # Apply model/rules configs
    try:
        if _MODEL_CFG:
            kp.set_model_config(_MODEL_CFG)
    except Exception:
        pass
    # Load time/relation rules and apply to KP
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs", "time_rules.json"), "r", encoding="utf-8") as f:
            kp.set_time_rules(_json.load(f) or {})
    except Exception:
        pass
    try:
        if _FEATURE_FLAGS:
            kp.set_feature_flags(_FEATURE_FLAGS)
    except Exception:
        pass
    # Narration: create narrator and attach to KP
    try:
        narrator = Narrator(_MODEL_CFG, _NARR_POLICY)
        # env keywords (scenes visual/sound/air/props)
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs", "narration_env.json"), "r", encoding="utf-8") as f:
            env_kw = _json.load(f) or {}
        scenes = env_kw.get("scenes") or {}
        if scenes:
            narrator.set_env_keywords(scenes)
        # Optional narrator debug logger: write raw LLM returns to run.log
        def _log_debug(line: str):
            try:
                log_fp.write(f"[NARR] {line}\n"); log_fp.flush()
            except Exception:
                pass
        if _FEATURE_FLAGS.get("log_narrator_debug"):
            narrator.set_debug_logger(_log_debug)
        kp.set_narrator(narrator)
    except Exception:
        pass
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs", "relation_rules.json"), "r", encoding="utf-8") as f:
            kp.set_relation_rules(_json.load(f) or {})
    except Exception:
        pass

    # Initialize scene & objectives for the escape storyline
    set_scene("旧城区·北侧仓棚", [
        "确认包围态势与盲区",
        "潜行接近突破口",
        "抵达撤离点 E-3",
    ])

    # If character config didn't provide player stat, ensure a sane default for player
    if "Doctor" not in WORLD.characters:
        set_dnd_character(
            name="Doctor",
            level=1,
            ac=14,
            abilities={"STR": 12, "DEX": 16, "CON": 14, "INT": 10, "WIS": 14, "CHA": 10},
            max_hp=12,
            proficient_skills=["perception", "stealth", "survival", "athletics"],
            proficient_saves=["STR", "DEX"],
        )

    # Note: story beats (docs/plot.story.json) will handle additional pressure/events


    # Prepare mutable NPC list (already built); ensure it excludes player and KP
    npcs_list = [a for a in npcs_list if getattr(a, "name", None) != getattr(player, "name", None)]

    # Optionally load a plot story and inject to KP (best-effort)
    _UNIT_CATALOG: dict[str, dict] = {}
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "plot.story.json"), "r", encoding="utf-8") as f:
            story = _json.load(f)
        if isinstance(story, dict):
            # cache unit catalog for spawn_by_id
            units = story.get("units") or []
            if isinstance(units, list):
                for u in units:
                    try:
                        uid = str(u.get("id"))
                        if uid:
                            _UNIT_CATALOG[uid] = dict(u)
                    except Exception:
                        pass
            kp.set_story(story)
    except Exception:
        _UNIT_CATALOG = {}

    # Assemble participants for MsgHub
    participants = list(participants_order) + [kp]

    # Prepare run log (overwrite each run)
    _root = os.path.dirname(os.path.dirname(__file__))
    _log_path = os.path.join(_root, "run.log")
    log_fp = open(_log_path, "w", encoding="utf-8")

    def _log_tag(tag: str, text: str):
        try:
            log_fp.write(f"[{tag}] {text}\n"); log_fp.flush()
        except Exception:
            pass

    # Attach KP debug logger so握手/决策摘要写入 run.log
    try:
        def _kp_dbg(line: str):
            try:
                log_fp.write(f"[KP] {line}\n"); log_fp.flush()
            except Exception:
                pass
        kp.set_debug_logger(_kp_dbg)
    except Exception:
        pass

    async def _bcast(msg: Msg):
        await hub.broadcast(msg)
        try:
            try:
                text = msg.get_text_content()
            except Exception:
                text = None
            if text is None:
                c = getattr(msg, "content", "")
                text = c if isinstance(c, str) else str(c)
            log_fp.write(f"{msg.name}: {text}\n")
            log_fp.flush()
        except Exception:
            pass

    async def _check_death_endings() -> bool:
        """If Doctor or Amiya HP <= 0, broadcast ending and stop the adventure."""
        try:
            snap = WORLD.snapshot()
            chars = snap.get("characters", {}) or {}
            def _hp(name: str) -> int:
                st = chars.get(name, {}) or {}
                try:
                    return int(st.get("hp", 0) or 0)
                except Exception:
                    return 0
            doc_hp = _hp("Doctor")
            ami_hp = _hp("Amiya")
            if doc_hp <= 0:
                await _bcast(Msg("Host", "仓棚阴影骤紧，呼号在铁皮墙间打转——博士倒地，通讯熄灭。", "assistant"))
                await _bcast(Msg("Host", "结局：撤离失败（玩家阵亡）", "assistant"))
                try:
                    tr = block_objective("抵达撤离点 E-3", "关键成员阵亡")
                    for blk in tr.content or []:
                        if blk.get("type") == "text":
                            await _bcast(Msg("Host", blk.get("text"), "assistant"))
                except Exception:
                    pass
                return True
            if ami_hp <= 0:
                await _bcast(Msg("Host", "微光在披风边缘熄灭，脚步声徒然空落——阿米娅停止了呼吸。", "assistant"))
                await _bcast(Msg("Host", "结局：突围终止（阿米娅阵亡）", "assistant"))
                try:
                    tr = block_objective("抵达撤离点 E-3", "关键成员阵亡")
                    for blk in tr.content or []:
                        if blk.get("type") == "text":
                            await _bcast(Msg("Host", blk.get("text"), "assistant"))
                except Exception:
                    pass
                return True
        except Exception:
            return False
        return False

    async with MsgHub(
        participants=participants,
        announcement=Msg(
            "Host",
            "旧城区·北侧仓棚。外围灯影交错，北与西面巡逻重叠；E-3 或许尚可通行。\n目标：确认包围态势与盲区、潜行接近突破口、抵达撤离点 E-3。\n提示：玩家直接发言；需要推进潜行/观察/越障/压制等，由 KP 以 skill_check_dnd/attack_roll_dnd 裁决。",
            "assistant",
        ),
    ) as hub:
        # Opening: all NPCs introduce in order
        await sequential_pipeline(npcs_list)
        # Note: 不在开场阶段进行 Player↔KP 握手；只在回合内进行，
        # 避免“确认后立刻又到玩家回合”的体验跳变。

        # Main loop: continue rounds until player quits
        round_idx = 1
        while True:
            # 主持人信息也打印到控制台，避免只进入消息总线而不显示
            _hdr = Msg("Host", f"第{round_idx}回合：玩家行动（输入 /quit 退出）", "assistant")
            await _bcast(_hdr)
            try:
                await kp.print(_hdr)
            except Exception:
                pass
            # Log current turn snapshot if in combat
            try:
                turn = get_turn()
                meta = turn.metadata or {}
                actor = meta.get("actor")
                rnd = meta.get("round")
                state = meta.get("state") or {}
                mv = state.get("move_left")
                a_used = state.get("action_used")
                b_used = state.get("bonus_used")
                r_avail = state.get("reaction_available")
                _log_tag("TURN", f"R{rnd} actor={actor} move_left={mv} action_used={a_used} bonus_used={b_used} reaction_avail={r_avail}")
            except Exception:
                pass
            _sum = Msg("Host", _world_summary_text(WORLD.snapshot()), "assistant")
            await _bcast(_sum)
            try:
                await kp.print(_sum)
            except Exception:
                pass
            should_end, player_final = await run_player_kp_handshake(hub, player, kp, _bcast)
            if should_end:
                await _bcast(Msg("Host", "本次冒险暂告一段落。", "assistant"))
                break
            # Immediately adjudicate confirmed player action
            if player_final is not None:
                judge_msgs = await kp.adjudicate([player_final])
                for m in judge_msgs:
                    await _bcast(m)
                # Death endings check after immediate adjudication
                if await _check_death_endings():
                    break
            # Allow KP as director to insert enemies/events based on story state
            await _maybe_director_actions(hub, kp, npcs_list, _bcast)
            # Death endings after director actions (e.g., scripted damage)
            if await _check_death_endings():
                break

            # NPCs act stepwise with immediate adjudication
            await run_npc_round_stepwise(hub, kp, npcs_list, _bcast)
            print("[system] world:", WORLD.snapshot())
            # Death endings after NPC round
            if await _check_death_endings():
                break
            round_idx += 1

        # Close log when finishing
        try:
            log_fp.close()
        except Exception:
            pass


async def run_player_kp_handshake(hub: MsgHub, player: PlayerAgent, kp: KPAgent, _bcast, max_steps: int = 8) -> tuple[bool, Msg | None]:
    """Run an isolated Player<->KP handshake:
    - Temporarily disable auto broadcast so raw Player输入与KP提案不会影响其他NPC；
    - 仅在确认“是”后，将最终改写后的 Player 发言广播给所有参与者。
    """
    # Temporarily disable auto broadcast so other agents won't observe raw inputs
    hub.set_auto_broadcast(False)
    try:
        steps = 0
        while steps < max_steps:
            # 1) Player speaks (no auto broadcast)
            out_p = await player(None)
            # Log raw player input (not broadcasted)
            try:
                try:
                    txt = out_p.get_text_content()
                except Exception:
                    txt = None
                if txt is None:
                    c = getattr(out_p, "content", "")
                    txt = c if isinstance(c, str) else str(c)
                if txt and len(txt) > 120:
                    txt = txt[:120] + "…"
                # write as PLAYER line
                _log_tag("PLAYER", txt)
            except Exception:
                pass
            # Handle /quit fast path
            if hasattr(player, "wants_exit") and callable(getattr(player, "wants_exit")):
                try:
                    if player.wants_exit():
                        await _bcast(out_p)
                        return True, out_p
                except Exception:
                    pass
            # /skip: 改为“直接改写并落地”，不再二次确认
            if hasattr(player, "wants_skip") and callable(getattr(player, "wants_skip")):
                try:
                    if player.wants_skip():
                        # 让 KP 直接改写为被动姿态，并返回最终 Player 消息
                        if hasattr(kp, "rewrite_skip_immediately"):
                            final_msg = await kp.rewrite_skip_immediately()
                            await _bcast(final_msg)
                            return False, final_msg
                except Exception:
                    pass
            # Deliver to KP only
            await kp.observe(out_p)

            # 2) KP responds (either clarification or confirmation proposal or final Player msg)
            out_kp = await kp(None)

            # If KP returned a finalized Player message, broadcast it to all and stop
            if getattr(out_kp, "name", "") == getattr(player, "name", "Player") and getattr(out_kp, "role", "") == "user":
                await _bcast(out_kp)
                return False, out_kp

            # Otherwise, deliver KP's assistant reply back to Player only (no broadcast)
            await player.observe(out_kp)

            steps += 1
    finally:
        # Re-enable auto broadcast for subsequent turns
        hub.set_auto_broadcast(True)
    return False, None


async def run_npc_round_stepwise(hub: MsgHub, kp: KPAgent, agents: list[ReActAgent], _bcast) -> None:
    """NPCs act one by one; after each action, KP adjudicates immediately and
    broadcasts results (including time advancement and events)."""
    hub.set_auto_broadcast(False)
    try:
        for a in agents:
            out = await a(None)
            await _bcast(out)
            judge_msgs = await kp.adjudicate([out])
            for m in judge_msgs:
                await _bcast(m)
    finally:
        hub.set_auto_broadcast(True)


def _world_summary_text(snap: dict) -> str:
    try:
        t = int(snap.get("time_min", 0))
    except Exception:
        t = 0
    hh, mm = t // 60, t % 60
    weather = snap.get("weather", "unknown")
    location = snap.get("location", "未知")
    objectives = snap.get("objectives", []) or []
    obj_status = snap.get("objective_status", {}) or {}
    rels = snap.get("relations", {}) or {}
    try:
        rel_lines = [f"{k}:{v}" for k, v in rels.items()]
    except Exception:
        rel_lines = []
    inv = snap.get("inventory", {}) or {}
    inv_lines = []
    try:
        for who, bag in inv.items():
            if not bag:
                continue
            inv_lines.append(f"{who}[" + ", ".join(f"{it}:{cnt}" for it, cnt in bag.items()) + "]")
    except Exception:
        pass
    # Characters
    chars = snap.get("characters", {}) or {}
    char_lines = []
    try:
        for nm, st in chars.items():
            hp = st.get("hp"); max_hp = st.get("max_hp")
            if hp is not None and max_hp is not None:
                char_lines.append(f"{nm}(HP {hp}/{max_hp})")
    except Exception:
        pass

    lines = [
        f"环境概要：地点 {location}；时间 {hh:02d}:{mm:02d}；天气 {weather}",
        ("目标：" + "; ".join((f"{str(o)}({obj_status.get(str(o))})" if obj_status.get(str(o)) else str(o)) for o in objectives)) if objectives else "目标：无",
        ("关系：" + "; ".join(rel_lines)) if rel_lines else "关系：无变动",
        ("物品：" + "; ".join(inv_lines)) if inv_lines else "物品：无",
        ("角色：" + "; ".join(char_lines)) if char_lines else "角色：未登记",
    ]
    return "\n".join(lines)


# Obsolete auto-resolver removed; KP.adjudicate handles results.


async def _maybe_director_actions(hub: MsgHub, kp: KPAgent, npcs_list: list[ReActAgent], _bcast) -> None:
    """Ask KP (as director) whether to do director actions now. Supports:
    - actions: list of typed actions (broadcast/spawn/add_objective/...)
    - fallback decision=spawn with 'spawn' list (LLM path)
    """
    try:
        actions = await kp.consider_director_actions()
    except Exception:
        return
    if not isinstance(actions, dict):
        return
    # Structured actions list (story-driven)
    act_list = actions.get("actions")
    if isinstance(act_list, list) and act_list:
        for a in act_list:
            if not isinstance(a, dict):
                continue
            t = str(a.get("type") or "")
            if t == "broadcast":
                txt = str(a.get("text") or "").strip()
                if txt:
                    await _bcast(Msg("Host", txt, "assistant"))
            elif t == "set_scene":
                # Director can switch scene and optionally reset/append objectives
                # {type:"set_scene", name:"地点名", objectives:[...], append: bool}
                name = a.get("name")
                objs = a.get("objectives")
                append = bool(a.get("append", False))
                if name:
                    try:
                        tr = set_scene(str(name), list(objs) if isinstance(objs, list) else None, append=append)
                        for blk in (tr.content or []):
                            if blk.get("type") == "text":
                                await _bcast(Msg("Host", blk.get("text"), "assistant"))
                    except Exception:
                        pass
            elif t == "spawn":
                units = a.get("units") or []
                if isinstance(units, list):
                    try:
                        _log_tag("SPAWN", f"units={len(units)} (by spec)")
                    except Exception:
                        pass
                    await _spawn_from_specs(hub, npcs_list, units)
            elif t == "spawn_by_id":
                ids = a.get("ids") or []
                if isinstance(ids, list) and ids:
                    # resolve specs from story units catalog
                    try:
                        _log_tag("SPAWN", "by_id=" + ",".join([str(x) for x in ids]))
                        await _spawn_from_ids(hub, npcs_list, [str(x) for x in ids])
                    except Exception:
                        pass
            elif t == "add_objective":
                nm = a.get("name")
                if nm:
                    tr = add_objective(str(nm))
                    for blk in tr.content or []:
                        if blk.get("type") == "text":
                            await _bcast(Msg("Host", blk.get("text"), "assistant"))
            elif t == "complete_objective":
                nm = a.get("name")
                note = a.get("note") or ""
                if nm:
                    tr = complete_objective(str(nm), str(note))
                    for blk in tr.content or []:
                        if blk.get("type") == "text":
                            await _bcast(Msg("Host", blk.get("text"), "assistant"))
            elif t == "block_objective":
                nm = a.get("name")
                reason = a.get("reason") or ""
                if nm:
                    tr = block_objective(str(nm), str(reason))
                    for blk in tr.content or []:
                        if blk.get("type") == "text":
                            await _bcast(Msg("Host", blk.get("text"), "assistant"))
            elif t == "schedule_event":
                name = a.get("name") or "(事件)"
                at = int(a.get("at_min") or WORLD.time_min)
                note = a.get("note") or ""
                eff = a.get("effects") or []
                schedule_event(str(name), at, str(note), effects=eff if isinstance(eff, list) else [])
            elif t == "relation":
                x = a.get("a")
                y = a.get("b")
                d = int(a.get("delta") or 0)
                reason = a.get("reason") or ""
                if x and y:
                    tr = change_relation(str(x), str(y), d, reason=str(reason))
                    for blk in tr.content or []:
                        if blk.get("type") == "text":
                            await _bcast(Msg("Host", blk.get("text"), "assistant"))
            elif t == "grant":
                target = a.get("target"); item = a.get("item"); n = int(a.get("n") or 1)
                if target and item:
                    tr = grant_item(str(target), str(item), int(n))
                    for blk in tr.content or []:
                        if blk.get("type") == "text":
                            await _bcast(Msg("Host", blk.get("text"), "assistant"))
            elif t == "damage":
                target = a.get("target"); amount = int(a.get("amount") or 0)
                if target:
                    from world.tools import damage as _damage
                    tr = _damage(str(target), int(amount))
                    for blk in tr.content or []:
                        if blk.get("type") == "text":
                            await _bcast(Msg("Host", blk.get("text"), "assistant"))
            elif t == "heal":
                target = a.get("target"); amount = int(a.get("amount") or 0)
                if target:
                    from world.tools import heal as _heal
                    tr = _heal(str(target), int(amount))
                    for blk in tr.content or []:
                        if blk.get("type") == "text":
                            await _bcast(Msg("Host", blk.get("text"), "assistant"))
        return
    # LLM fallback path with decision=spawn
    if actions.get("decision") == "spawn":
        spawn_list = actions.get("spawn") or []
        if isinstance(spawn_list, list) and spawn_list:
            bc = actions.get("broadcast")
            if isinstance(bc, str) and bc.strip():
                await _bcast(Msg("Host", bc.strip(), "assistant"))
            await _spawn_from_specs(hub, npcs_list, spawn_list)

async def _spawn_from_specs(hub: MsgHub, npcs_list: list[ReActAgent], units: list[dict]) -> None:
    for spec in units:
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or _auto_enemy_name())
        kind = str(spec.get("kind") or "raider")
        target_pref = str(spec.get("target_pref") or "Doctor")
        ac = int(spec.get("ac") or 13)
        hp = int(spec.get("hp") or 9)
        abilities = spec.get("abilities") or {"STR": 12, "DEX": 12, "CON": 12, "INT": 8, "WIS": 10, "CHA": 8}
        dmg = str(spec.get("damage_expr") or "1d6+STR")
        persona = str(spec.get("persona") or _default_enemy_persona(kind))
        try:
            set_dnd_character(name=name, level=1, ac=ac, abilities=abilities, max_hp=hp)
        except Exception:
            pass
        try:
            allowed_names_str = ", ".join(list(WORLD.characters.keys()))
        except Exception:
            allowed_names_str = "Doctor, Amiya"
        agent = make_enemy_npc(name=name, persona=persona, default_damage_expr=dmg, target_pref=target_pref, prompt_template=_ENEMY_PROMPT_TEMPLATE, allowed_names=allowed_names_str)
        npcs_list.append(agent)

async def _spawn_from_ids(hub: MsgHub, npcs_list: list[ReActAgent], ids: list[str]) -> None:
    # Pull specs from cached story units
    try:
        allowed_names_str = ", ".join(list(WORLD.characters.keys()))
    except Exception:
        allowed_names_str = "Doctor, Amiya"
    for sid in ids:
        spec = None
        try:
            spec = _UNIT_CATALOG.get(str(sid))  # type: ignore[name-defined]
        except Exception:
            spec = None
        if not isinstance(spec, dict):
            await hub.broadcast(Msg("Host", f"[忽略] 未在名册中定义的单位：{sid}", "assistant"))
            continue
        name = str(spec.get("id") or sid)
        kind = str(spec.get("kind") or "raider")
        target_pref = str(spec.get("target_pref") or "Doctor")
        ac = int(spec.get("ac") or 13)
        hp = int(spec.get("hp") or 9)
        abilities = spec.get("abilities") or {"STR": 12, "DEX": 12, "CON": 12, "INT": 8, "WIS": 10, "CHA": 8}
        dmg = str(spec.get("damage_expr") or "1d6+STR")
        persona = str(spec.get("persona") or _default_enemy_persona(kind))
        try:
            set_dnd_character(name=name, level=1, ac=ac, abilities=abilities, max_hp=hp)
        except Exception:
            pass
        agent = make_enemy_npc(name=name, persona=persona, default_damage_expr=dmg, target_pref=target_pref, prompt_template=_ENEMY_PROMPT_TEMPLATE, allowed_names=allowed_names_str)
        npcs_list.append(agent)
    # Enter combat automatically when enemies出现
    try:
        from world.tools import start_combat as _start_combat
        res = _start_combat()
        try:
            meta = res.metadata or {}
            order = meta.get('initiative') or []
            scores = meta.get('scores') or {}
            parts = []
            for n in order:
                sc = scores.get(n)
                parts.append(f"{n}({sc})")
            _log_tag('COMBAT-START', 'initiative=' + ', '.join(parts))
        except Exception:
            pass
        for blk in res.content or []:
            if isinstance(blk, dict) and blk.get('type') == 'text':
                await _bcast(Msg('Host', blk.get('text'), 'assistant'))
    except Exception:
        pass

_enemy_auto_id = 1
def _auto_enemy_name() -> str:
    global _enemy_auto_id
    nm = f"Enemy{_enemy_auto_id}"
    _enemy_auto_id += 1
    return nm

def _default_enemy_persona(kind: str) -> str:
    k = (kind or "").lower()
    if k in ("guard", "patrol"):
        return "近卫巡逻兵。先盘查证件，遇挑衅或逃跑则强制制服。"
    if k == "sniper":
        return "远程火力支援手。偏好远距离压制，优先打击高威胁目标。"
    return "整合运动突击手。行动果断，先威吓索要通行与物资，遭拒或反抗即近身攻击。"

def make_enemy_npc(name: str, persona: str, default_damage_expr: str = "1d6+STR", target_pref: str = "Doctor", prompt_template: str | None = None, allowed_names: str | None = None) -> ReActAgent:
    """Create an LLM-backed enemy NPC with stronger nudges to include attack intents.
    Enemy talks 1-2 sentences then output a JSON intent; when attacking, include damage_expr.
    """
    api_key = os.environ["MOONSHOT_API_KEY"]
    base_url = _MODEL_CFG.get("base_url") or os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    sec = _MODEL_CFG.get("npc") or _MODEL_CFG
    model_name = sec.get("model") or os.getenv("KIMI_MODEL", "kimi-k2-turbo-preview")

    tools = "describe_world()"
    intent_schema = (
        "{\n  \"intent\": \"attack|talk|investigate|move|assist|wait\",\n  \"target\": \"目标名称\",\n"
        "  \"ability\": \"STR|DEX|...\",\n  \"proficient\": true,\n  \"dc_hint\": 12,\n  \"damage_expr\": \"%s\",\n  \"time_cost\": 1,\n  \"notes\": \"一句话说明意图\"\n}" % (default_damage_expr,)
    )
    sys_prompt = None
    tpl = prompt_template or _ENEMY_PROMPT_TEMPLATE
    if tpl:
        try:
            sys_prompt = tpl.format(name=name, persona=persona, target_pref=target_pref, default_damage_expr=default_damage_expr, tools=tools, intent_schema=intent_schema, allowed_names=(allowed_names or "Doctor, Amiya"))
        except Exception:
            sys_prompt = None
    if not sys_prompt:
        sys_prompt = f"你是敌对NPC：{name}。人设：{persona}。\n" + (
            f"""对话要求：
- 先用简短中文说1句对白/威吓/动作，符合人设。
- 然后给出一个 JSON 意图（不要调用工具；裁决由KP执行）。
- 当你决定攻击时，请在 JSON 中包含 damage_expr（通常为 {default_damage_expr}）。
- 目标偏好：{target_pref}（若该目标不在场，可选择最近或威胁更高者）。
- 参与者名称（仅可用）：{allowed_names}
- 意图JSON的 target 必须从上述列表中选择；禁止使用别称/编号（如 Player/玩家/Player1）。
JSON格式（仅保留需要的字段）：
{intent_schema}
输出示例：
{name}低喝：交出通行证！
```json
{{"intent":"attack","target":"{target_pref}","ability":"STR","proficient":false,"damage_expr":"{default_damage_expr}","notes":"短棍攻击"}}
```
"""
        ).format(intent_schema=intent_schema, allowed_names=(allowed_names or "Doctor, Amiya"))

    model = OpenAIChatModel(
        model_name=model_name,
        api_key=api_key,
        stream=bool(sec.get("stream", True)),
        client_args={"base_url": base_url},
        generate_kwargs={"temperature": float(sec.get("temperature", 0.7))},
    )
    toolkit = Toolkit()
    toolkit.register_tool_function(describe_world)
    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=model,
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
        toolkit=toolkit,
    )


if __name__ == "__main__":
    banner()
    try:
        asyncio.run(tavern_scene())
    except KeyboardInterrupt:
        sys.exit(130)
