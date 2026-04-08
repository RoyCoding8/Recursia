# Backend

FastAPI backend for recursive orchestration, checker policies, merge flow, and SSE run events.

## Quick start

```powershell
cd "D:/AI/Recursia/backend"
uv sync
```

## Environment setup (provider placeholders)

Copy the template once:

```powershell
cd "D:/AI/Recursia/backend"
Copy-Item .env.example .env
```

Open `backend/.env` and set values for **one** provider only:

- `LLM_PROVIDER=stub` (safe local deterministic default)
- `LLM_PROVIDER=gemini` and fill Gemini fields
- `LLM_PROVIDER=groq` and fill Groq fields
- `LLM_PROVIDER=bedrock` and fill Bedrock fields

For Bedrock SigV4, required fields are simplified to:

- `AWS_REGION=...`
- `AWS_ACCESS_KEY_ID=...`
- `AWS_SECRET_ACCESS_KEY=...`
- `BEDROCK_MODEL_ID=...`

### Browser CORS configuration

For browser calls to backend routes (for example `POST /api/runs`), configure allowed origins with:

- `BACKEND_CORS_ORIGINS` as a comma-separated list of exact origins.
- Example: `BACKEND_CORS_ORIGINS=http://127.0.0.1:3000,http://localhost:3000`

Default behavior when unset/empty:

- `http://127.0.0.1:3000`
- `http://localhost:3000`

Security notes:

- Wildcard (`*`) origins are intentionally rejected when credentials are enabled.
- Keep this list minimal and explicit in production.

Runtime precedence behavior:

- Backend resolves config from both process environment and `backend/.env`.
- Precedence is controlled by `BACKEND_ENV_PRECEDENCE`:
  - `os_wins` (default): existing process environment variables override `.env`.
  - `dotenv_wins`: `.env` overrides existing process environment variables.
- Use `dotenv_wins` for deterministic local launcher behavior when stale shell env vars are common.
- If `backend/.env` is missing, startup continues (defaults/external env are used).
- Supported `.env` lines are lightweight `KEY=VALUE` entries (blank lines and `#` comments are ignored).

## Readiness and config diagnostics

The backend now exposes system diagnostics endpoints:

- `GET /health` — process liveness only.
- `GET /ready` — provider/runtime readiness gate. Returns non-200 with actionable reason if provider config/runtime is unhealthy.
- `GET /system/config-summary` — non-secret effective config snapshot:
  - `provider`
  - `resolved_model`
  - `resolved_model_source`
  - `fallback_mode`
  - `env_precedence`
  - `cors_origins`

### Troubleshooting startup/env drift

- Symptom: `/health` is 200 but `POST /api/runs` fails with 500.
  - Check `/ready`; it now reports provider readiness failures directly.
- Symptom: provider unexpectedly resolves to a stale value from your shell/session.
  - Check `/system/config-summary` (`provider`, `env_precedence`).
  - For local deterministic behavior, set `BACKEND_ENV_PRECEDENCE=dotenv_wins` in `.env`.
- Symptom: Bedrock selected but backend is not ready.
  - Ensure all required Bedrock vars are set (`AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `BEDROCK_MODEL_ID`).

Important:

- The values in `.env.example` are placeholders for configuration onboarding.
- Do not commit real keys or secrets.
- Provider behavior wiring is handled in later realignment steps.

Run API locally:

```powershell
cd "D:/AI/Recursia/backend"
uv run uvicorn main:app --reload
```

## Test commands (CI-friendly)

```powershell
cd "D:/AI/Recursia/backend"
uv run pytest -q
```

Focused integration checks:

```powershell
cd "D:/AI/Recursia/backend"
uv run pytest tests/integration/test_full_run_pipeline.py -q
uv run pytest tests/integration/test_checker_fail_x3_hitl.py -q
uv run pytest tests/integration/test_sse_reconnect_replay.py -q
```

## Observability helpers

- `app/observability/logging.py`: structured JSON logging helpers (`configure_structured_logging`, `log_event`, contextual logger binding).
- `app/observability/metrics.py`: lightweight in-memory metrics recorder for TTFT, node duration, checker pass/fail, retry, and blocked-human counters.

These modules are intentionally dependency-light and can be wired into services incrementally.

## State repository adapters

The backend exposes a repository abstraction at `app/state/repository.py` with two implementations:

- `InMemoryRunStateRepository` (`app/state/memory_repo.py`) for MVP/in-process runs.
- `SQLiteRunStateRepository` (`app/state/sqlite_repo.py`) for durable v1-ready persistence and replay.

### SQLite adapter usage

```python
from app.state.sqlite_repo import SQLiteRunStateRepository

repo = SQLiteRunStateRepository(db_path="./data/recursia.sqlite3")
```

Notes:

- On initialization, the adapter applies `app/state/sql/schema.sql` automatically.
- Durable tables cover `runs`, `nodes`, `attempts`, `events`, and `interventions`.
- Event replay is supported via `list_run_events(run_id, after_seq=...)`.

### Switching memory vs SQLite

- For local fast tests or ephemeral runs: instantiate `InMemoryRunStateRepository`.
- For resumable sessions across process restarts: instantiate `SQLiteRunStateRepository` with a stable DB path.

## Known constraints

- MVP runtime is single-process by default; in-memory repository is not cross-process durable.
- SSE replay is sequence (`seq`) based; explicit `event_id` dedupe storage is not implemented.
- Install backend dependencies before tests/import checks (`fastapi` must be present in the active env).
