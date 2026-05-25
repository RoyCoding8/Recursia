# Extension Points

How to extend Recursia without bloating it.

## Adding a Persona

1. Create `personas/my_persona.md` with YAML frontmatter:

```markdown
---
id: my_persona
name: My Custom Persona
description: One-line description
temperature: 0.3
---

You are a specialist in X. Your approach is Y.

When executing tasks:
- Always Z
- Never W
```

2. Restart the backend. `PersonaRegistry.reload()` discovers it automatically.
3. Pass `base_persona_id: "my_persona"` when creating a run, or let the router select it.

## Adding an LLM Provider

1. Implement the `LLMClient` protocol in `app/adapters/llm_client.py`:

```python
class MyProviderClient(LLMClient):
    def generate_json(self, request: LLMGenerateRequest) -> LLMResult:
        # Call your provider's API
        # Parse response into LLMResult(data=dict, usage=LLMUsage(...))
        ...
```

2. Register in `app/adapters/llm_factory.py`:

```python
elif provider == "my_provider":
    return MyProviderClient(model=config.llm_model, ...)
```

3. Set `LLM_PROVIDER=my_provider` in `.env`.

## Adding a Checker Backend

1. Implement the checker client protocol:

```python
class MyCheckerClient:
    def evaluate(self, request: CheckerRequest) -> dict[str, Any]:
        # Return: {verdict, reason, suggested_fix, confidence, violations}
        ...
```

2. Wire in `app/__init__.py`:

```python
if my_condition:
    checker_client = MyCheckerClient(...)
else:
    checker_client = LLMCheckerClient(llm_client=llm_client)
checker = CheckerService(checker_client=checker_client)
```

## Adding a Sandbox Backend

1. Implement the sandbox protocol in `app/sandbox/executor.py`:

```python
class MySandboxBackend:
    def run_tests(self, code: str, tests: list[TestCase], *, timeout_s: float) -> SuiteResult:
        ...
    def run_code(self, code: str, *, language: str, stdin: str, timeout_s: float) -> ExecResult:
        ...
```

2. Register in `create_sandbox()`:

```python
elif backend == "my_sandbox":
    return MySandboxBackend(...)
```

## Adding Domain Events

1. Add to `DomainEventType` enum in `app/domain/events.py`:

```python
MY_EVENT = "my.event"
```

2. Emit from any service that has access to `event_stream`:

```python
self._emit(run_id, node_id, DomainEventType.MY_EVENT, {"key": "value"})
```

3. Handle in frontend `runStore.ts`:

```python
case "my.event":
    // update state
    break;
```

## Adding a Transition

1. Add the transition to the map in `app/domain/policies.py`:

```python
_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    ...
    RunStatus.MY_STATUS: frozenset({RunStatus.NEXT_STATUS}),
}
```

2. Add the `ensure_*_transition()` function:

```python
def ensure_my_transition(state: RunState) -> RunState:
    return _apply_run_transition(state, RunStatus.MY_STATUS)
```

## Modifying the Complexity Estimator

The estimator in `app/services/complexity.py` uses keyword heuristics. To tune:

- `_COMPLEXITY_KEYWORDS` — add/remove keywords per tier
- `_MULTI_STEP_MARKERS` — conjunctions that signal compound tasks
- `_score_text()` — adjust weights
- `_suggest_depth()` — adjust the depth mapping function

No LLM calls. Pure heuristics. Fast.

## Adding Frontend Components

1. Create component in `src/components/`
2. Import in `src/app/page.tsx`
3. Use `useRunStore(selector)` for selective state access
4. Use `runStore.method()` for direct state mutations

The Zustand store has backward-compatible `runStore` singleton for imperative code (API calls, SSE handlers).
