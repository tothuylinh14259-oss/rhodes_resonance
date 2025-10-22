Common dev commands:
- Run app: python src/main.py
- Run app as server: python src/main.py --host 0.0.0.0 --port 8000 --cors http://localhost:3000
- Install dev deps: pip install -e .[dev]
- Lint (ruff): ruff check .
- Format (ruff): ruff format .
- Type check (mypy): mypy .
- Run tests: pytest -q
- Set env vars (example):
  export OPENAI_API_KEY=your_key
  export OPENAI_BASE_URL=https://api.your-llm.com/v1
  export OPENAI_MODEL=gpt-4o-mini
- Alternative env var fallbacks supported: API_KEY/MOONSHOT_API_KEY, BASE_URL/KIMI_BASE_URL, MODEL/KIMI_MODEL.