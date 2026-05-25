# Subtle Details

Things that aren't obvious from reading the code casually.

## Executor

### `_active` dict pattern

The `_active` dict in `runs.py` replaces what used to be multiple global singletons with locks. It's populated either by the lifespan (production) or by `set_runs_services()` (tests). The key `"repository"` is used as a sentinel — if it's present, the lifespan skips building services (prevents overwriting test-injected mocks).

### Cancellation threading model

`threading.Event` is checked between node executions, not within them. A long-running LLM call will complete before the cancellation takes effect. This is by design — interrupting mid-call would leave state inconsistent.

### Crash recovery

`_recover_interrupted_runs()` runs on startup and marks any runs with status RUNNING as FAILED. It only runs when the lifespan itself built the services (not when tests injected them). The `_recover_interrupted_runs` flag prevents double-recovery.

### Persona chain vs single persona

When `persona_chain` is null (default), the executor uses `node.persona_id` (single persona). When set, it loops through the chain sequentially. Between personas, the prior output is serialized to JSON (capped at 2000 chars) and passed as `NodeContext.prior_persona_output`. The `child()` method inherits both `prior_persona_output` and `checker_feedback` from the parent context.

### Cost-aware routing fallback

`_MODEL_TIERS` is populated from env vars at import time. If a tier's env var is unset, the value is `None`, which means the LLM client uses its default model. Unknown tier keys (e.g., from a future complexity estimator change) also fall back safely via `_MODEL_TIERS.get(tier)` returning `None`.

### Token budget check timing

The budget check happens twice: at the START of `execute_node` (before any work), and after `_persist_usage()` flushes tokens at the end. If `tokens_used >= max_total_tokens`, the node fails. The entry check uses the previously-flushed total; the post-execution check catches overruns from the current node's LLM calls.

## Checker

### Sandbox vs LLM fallback

The checker tries sandbox execution first (if enabled and code is found). If no code is extracted or sandbox is disabled, it falls back to `LLMCheckerClient`. The LLM fallback is a separate code path, not a degraded version of the sandbox checker.

### Checker feedback injection

When a checker fails and retries are allowed, `build_checker_feedback()` in `checker_handler.py` creates a feedback string from the `CheckerResult`. This is injected into `NodeContext.checker_feedback`, which gets included in the worker's prompt on the next attempt. The worker sees "CHECKER FEEDBACK (fix this): ..." in its context.

### Confidence scoring

Checker confidence ranges from 0.5 to 0.95. It's computed as `min(0.5 + (total_tests * 0.1), 0.95)`. More tests = higher confidence. A single test gives 0.6 confidence. Five or more tests cap at 0.95.

## Divider

### Multi-candidate generation

For complex tasks (score >= complexity_threshold), the divider generates `decomposition_candidates` candidates at elevated temperature (0.7). Each is scored by `score_decomposition()` — a pure function that evaluates rationale length, child count, interface contracts, and duplicate detection. The highest-scoring candidate wins.

### Forced base case

If the divider returns RECURSIVE_CASE but the node is at max depth, the executor forces a BASE_CASE via `_forced_base_case_result()`. This emits a `node.depth_limit_reached` event with the reason.

## State

### Repository abstraction

The `RunStateRepository` abstract class has 25+ methods. Both `InMemoryRunStateRepository` and `SQLiteRunStateRepository` implement the full interface. The in-memory version uses `dataclasses.replace()` for immutable updates. The SQLite version uses raw SQL with `RETURNING` clauses.

### Event replay

Events are persisted with monotonically increasing sequence numbers (per run). SSE clients can reconnect with `Last-Event-Id` header to replay missed events. The replay is sequence-based, not event-id-based (event IDs are UUIDs for dedup, not for ordering).

### Node context immutability

`NodeContext` is a frozen dataclass. Every mutation (`with_sibling_output()`, `with_checker_feedback()`, `with_prior_output()`, `child()`) returns a NEW instance. The original is never modified. This prevents subtle bugs where shared context gets mutated across parallel branches.

## Frontend

### Zustand store structure

The store has both state and actions in a single `create<RunState & RunActions>()` call. For backward compatibility, `runStore` is a singleton with the same methods, used by imperative code (API calls, SSE handlers). Components should use `useRunStore(selector)` for selective re-renders.

### SSE event types

`SSE_EVENT_TYPES` in `events.ts` must include ALL event types the store handles. Missing types cause events to only arrive via the generic `onmessage` handler, not named event listeners. Both `node.subtree_pruned` and `token.usage_recorded` are handled by the store but were previously missing from this array.

### ErrorBoundary

The `ErrorBoundary` component catches React rendering errors. It uses `getDerivedStateFromError` for synchronous state updates and `componentDidCatch` for logging. It does NOT catch errors in event handlers, async code, or SSR — only render errors.

## Config

### `BACKEND_ENV_PRECEDENCE`

When set to `os_wins` (default), existing process environment variables override `.env` values. When set to `dotenv_wins`, `.env` takes priority. Use `dotenv_wins` for deterministic local launcher behavior when stale shell env vars are common.

### `FORCE_STUB_MODE`

An environment variable used by the launcher scripts. When set, forces `LLM_PROVIDER=stub` regardless of `.env` settings. Not read by the backend code directly — the launcher sets `LLM_PROVIDER` before starting the server.
