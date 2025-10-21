General style:
- Python 3.11 with type hints where practical (not strictly enforced).
- Dataclasses for structured types (events/logging).
- Logging via a custom EventBus and structured logger; avoid raising in logging paths.
- Follow existing code patterns; keep functions pure where possible.
- Keep comments minimal; only add comments for non-obvious logic.
- Use descriptive variable names; prefer immutable local variables.

Tooling conventions:
- Ruff line-length 100; target python version 3.11. world/tools.py is excluded from ruff/mypy.
- Mypy is configured non-strict but with warnings enabled; avoid adding type errors.

Project-specific conventions:
- Prompts and context policies are centralized at the top of src/main.py; modify those to change model input.
- Tools are registered via Agentscope Toolkit; actions are thin wrappers delegating to world.tools validated dispatcher.
- Environment configuration: use env vars for external API settings (MOONSHOT_API_KEY, KIMI_BASE_URL, KIMI_MODEL).