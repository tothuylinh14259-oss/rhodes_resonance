Python 3.11 Agentscope-based NPC chat demo. The orchestrator is src/main.py, which defines prompt/context policies, loads JSON configs (configs/*.json), constructs Kimi (OpenAI-compatible) agents with Agentscope’s OpenAIChatModel, wraps world tools into callable actions, and runs the round-based runtime. Optional FastAPI/uvicorn server provides REST+WebSocket control, multi-session, and a basic static web frontend (web/).

Key modules:
- src/main.py: main orchestrator; prompt policies; settings loader; agent factory (make_kimi_npc); world actions; runtime loop; FastAPI app & CLI.
- src/world/tools.py: world model and mechanics (positions, relations, D&D-like stats, weapons, arts, combat resolution, status effects, scheduling, etc.).
- configs/: story, characters, weapons, arts, model config JSONs.
- tests/: world mechanics and logging tests; includes Agentscope stubs for unit tests.

Agent model integration:
- Uses Agentscope OpenAIChatModel with Kimi’s OpenAI-compatible API; API key, base URL, and model name are taken from environment variables.

Dev tooling:
- Ruff (lint + format), mypy (type checking), pytest.
- Pre-commit hooks defined in .pre-commit-config.yaml.

Entrypoints:
- CLI single run: python src/main.py --once
- Server mode: python src/main.py (requires fastapi & uvicorn)

Logs:
- logs/run_events.jsonl (structured), logs/run_story.log (human-readable), logs/prompts/*.txt (debug dumps).