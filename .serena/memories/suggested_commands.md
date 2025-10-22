Core dev commands:
- Run single demo in CLI: python src/main.py --once
- Run server (requires FastAPI/uvicorn): python src/main.py
- Install dev deps (pip): pip install -e .[dev]
- Install env via conda: conda env create -f environment.yml && conda activate npc-talk

Testing & quality:
- Run tests: pytest
- Lint (ruff): ruff check .
- Format (ruff): ruff format .
- Type check (mypy): mypy .
- Pre-commit: pre-commit install && pre-commit run -a

Environment variables (must export before running):
- MOONSHOT_API_KEY=<your_kimi_api_key>
- KIMI_BASE_URL=https://api.moonshot.cn/v1 (optional)
- KIMI_MODEL=kimi-k2-turbo-preview (optional)