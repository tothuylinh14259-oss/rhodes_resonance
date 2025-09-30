# NPC Talk Demo (Agentscope)

Strict demo for NPC group chat + story driving using real AgentScope (no stub fallback).

## Quick Start (Conda recommended)

```bash
# one-time: create env
cd /Users/administrator/syncdisk/npc_talk_demo
conda env create -f environment.yml
conda activate npc-talk

# run
python src/main.py
```

Expected: two NPCs introduce themselves at a tavern; a blacksmith joins; world state advances time and updates a relation.

This demo requires real AgentScope with `agentscope.pipeline` available.

## Layout

```
npc_talk_demo/
  src/
    main.py                 # Entry point: runs the tavern scene
    mini_agentscope/        # Tiny fallback so the demo runs offline
      __init__.py
      message.py
      pipeline.py
    agents/
      npc.py                # Simple deterministic NPC agent
    world/
      tools.py              # Minimal world state + helper tools
  docs/
    spec.md                 # 项目规范（设计、接口、约定）
  environment.yml           # Conda env spec (Python 3.11 + AgentScope from GitHub)
```

## Next Steps
- Replace `SimpleNPCAgent` with your LLM-based agent (inject persona, memory, toolkit).
- Add a Director agent to drive plot nodes and tool calls.
- Enforce structured JSON output and add a small validator + retry.
- Integrate your game engine via MCP/HTTP tools (spawn/move/query APIs).
