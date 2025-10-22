Before finishing a ticket:
- Ensure only files under /home/engine/project are changed, on the pre-created branch.
- Run finish tool to trigger linting/tests; fix failures rather than disabling hooks.
- Keep changes idiomatic; respect line-length 100 and typing hints.
- Do not modify CI/deployment (e.g., render.yaml, workflows) unless explicitly asked.
- Ensure .gitignore exists (repo already has one).
- Verify environment variable usage precedence as documented.
- Update README/docs only if necessary and consistent with functionality.