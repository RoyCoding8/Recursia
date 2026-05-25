# Configuration Reference

All configuration options for Recursia.

## Backend Environment Variables

### LLM Provider

| Variable | Type | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | string | `bedrock` | Provider: `gemini`, `groq`, `bedrock`, `stub` |
| `LLM_MODEL` | string | — | Override provider's default model |
| `LLM_TEMPERATURE` | float | `0.0` | Default temperature for LLM calls |
| `LLM_TIMEOUT_SECONDS` | int | `120` | Timeout for LLM calls |
| `LLM_MAX_RETRIES` | int | `2` | Max retries for schema validation failures |

### AWS Bedrock

| Variable | Type | Default | Description |
|---|---|---|---|
| `AWS_REGION` | string | `us-east-1` | AWS region |
| `AWS_ACCESS_KEY_ID` | string | — | AWS access key (or use IAM role) |
| `AWS_SECRET_ACCESS_KEY` | string | — | AWS secret key (or use IAM role) |
| `BEDROCK_MODEL_ID` | string | `anthropic.claude-sonnet-4-20250514-v1:0` | Bedrock model ARN |

### Google Gemini

| Variable | Type | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | string | — | Google Gemini API key |
| `GEMINI_MODEL` | string | `gemini-2.5-flash` | Gemini model name |

### Groq

| Variable | Type | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | string | — | Groq API key |
| `GROQ_MODEL` | string | `llama-3.3-70b-versatile` | Groq model name |

### Cost-Aware Routing

| Variable | Type | Default | Description |
|---|---|---|---|
| `LLM_MODEL_TIER_FAST` | string | — | Model for trivial tasks (complexity < 0.3) |
| `LLM_MODEL_TIER_STANDARD` | string | — | Model for medium tasks (0.3–0.7) |
| `LLM_MODEL_TIER_STRONG` | string | — | Model for complex tasks (complexity > 0.7) |

When unset, all tiers use the provider's default model.

### Database

| Variable | Type | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | string | `sqlite:///recursia.db` | Connection string |

Supported: `sqlite:///path` or `postgresql://user:pass@host:port/db`.

### Sandbox

| Variable | Type | Default | Description |
|---|---|---|---|
| `SANDBOX_ENABLED` | bool | `true` | Enable execution-based verification |
| `SANDBOX_BACKEND` | string | `local` | `local` (subprocess) or `docker` (epicbox) |

### CORS

| Variable | Type | Default | Description |
|---|---|---|---|
| `BACKEND_CORS_ORIGINS` | string | `http://127.0.0.1:3000,http://localhost:3000` | Comma-separated allowed origins |

### Env Precedence

| Variable | Type | Default | Description |
|---|---|---|---|
| `BACKEND_ENV_PRECEDENCE` | string | `os_wins` | `os_wins` or `dotenv_wins` |

## RunConfig (API)

Passed in `POST /api/runs` body as `config`.

### Checker

| Field | Type | Default | Description |
|---|---|---|---|
| `checker.enabled` | bool | `true` | Enable checker evaluation |
| `checker.node_level` | bool | `true` | Check at node level |
| `checker.merge_level` | bool | `true` | Check at merge level |
| `checker.max_retries_per_node` | int | `3` | Max checker retries before HITL |
| `checker.onCheck_fail` | string | `auto_retry` | `pause` (HITL) or `auto_retry` |

### Decomposition

| Field | Type | Default | Description |
|---|---|---|---|
| `max_depth` | int | `8` | Maximum recursion depth |
| `max_children_per_node` | int | `10` | Max children per decomposition |
| `decomposition_candidates` | int | `3` | Candidates for multi-candidate generation |
| `re_decompose_after` | int | `2` | Re-decompose after N checker failures |
| `complexity_threshold` | float | `0.6` | Threshold for multi-candidate mode |

### Adaptive Features

| Field | Type | Default | Description |
|---|---|---|---|
| `adaptive_depth` | bool | `false` | Use complexity estimator's suggested depth |
| `persona_chain` | list[string] | `null` | Sequential persona IDs (each refines prior) |

### Token Budget

| Field | Type | Default | Description |
|---|---|---|---|
| `token_budget.max_total_tokens` | int | `500000` | Per-run token cap |
| `token_budget.max_tokens_per_node` | int | `50000` | Per-node token cap |
| `token_budget.on_exhausted` | string | `fail` | `fail` or `warn` |

### Stream

| Field | Type | Default | Description |
|---|---|---|---|
| `stream.mode` | string | `sse` | `sse` or `websocket` |

### Workspace

| Field | Type | Default | Description |
|---|---|---|---|
| `workspace.root` | string | — | Workspace root path |
| `workspace.write_policy` | string | `propose` | `propose` or `direct` |

## Frontend Environment Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | string | `http://127.0.0.1:8000` | Backend API URL |
