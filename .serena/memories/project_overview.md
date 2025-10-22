Purpose: Agentscope-driven ReAct NPC chat demo with a tactical world model and round-based runtime. Main orchestrator is src/main.py which defines prompts, loads JSON configs, builds OpenAI-compatible Kimi agents, wraps world tools into actions, and can optionally serve a FastAPI server.

Tech stack: Python 3.11, Agentscope (installed from Git), pytest, ruff, mypy. Configs in configs/*.json for characters, story, weapons, arts, model. Logs written to logs/.

Structure:
- src/main.py: Orchestrator, agent factory (OpenAIChatModel), actions, world integration, server entry.
- src/world/tools.py: World state and game mechanics (movement, combat, relations, items, status, scheduling, etc.).
- configs/: characters.json, story.json, weapons.json, arts.json (optional), model.json, prompts.json (optional).
- tests/: unit tests for world tools and logging behavior.
- README.md: usage, env vars, architecture.

Entrypoint: python src/main.py (optionally with server flags --host/--port/--cors).