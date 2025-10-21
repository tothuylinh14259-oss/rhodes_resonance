Before finishing a ticket:
- Ensure changes are limited to /home/engine/project and current branch.
- Run through linting/formatting/type checks via the platform’s finish action; fix any reported issues.
- Keep env var usage consistent (MOONSHOT_API_KEY, KIMI_BASE_URL, KIMI_MODEL) for API integration.
- Don’t modify CI/CD workflow files unless explicitly requested.
- Maintain existing code patterns and minimal commenting.
- Verify .gitignore exists (it does).