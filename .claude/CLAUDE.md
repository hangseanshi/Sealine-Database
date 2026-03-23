# CLAUDE.md

## Critical Rules

- **NEVER** add any system prompt or user prompt content directly to `.py` files
  (e.g., `agent.py`, `message.py`).
- If an additional prompt is needed and it is **data-related**, add it to:
  `~/MEMORY/database_ai_schema.md`
- If an additional prompt is needed and it is **not data-related**, add it to:
  `~/MEMORY/system_prompt.md`

- **Start Server**
  - Always start the server in the background.
  - **Default** (OpenAI API):
```bash
    AI_PROVIDER=openai python -m flask --app server.app run --port 8080
```
  - **Claude API** (only when the user explicitly requests it):
```bash
    AI_PROVIDER=claude python -m flask --app server.app run --port 8080
```

- **Stop Server** — execute in this order:
  1. Kill all the background processes in /tasks list.
  2. Kill all Python processes.
  3. Verify no more Python process running.
  4. Check for any cached data from previous sessions and clear it so the next session starts fresh.