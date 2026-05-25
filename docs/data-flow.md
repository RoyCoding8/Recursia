# Data Flow

How an objective becomes a result.

## Run Lifecycle

```
1. POST /api/runs { objective, config, base_persona_id }
   │
   ├─ Orchestrator.create_run()
   │   ├─ Create RunState (QUEUED → RUNNING)
   │   ├─ Create root NodeState (QUEUED)
   │   ├─ Emit RUN_CREATED, NODE_CREATED, RUN_STATUS_CHANGED, NODE_STATUS_CHANGED
   │   └─ Return run_id, root_node_id
   │
   └─ Background: executor.submit(execute_node, root_node_id)

2. execute_node(node_id)
   │
   ├─ Budget check: tokens_used >= max_total_tokens? → fail
   ├─ record_node_started → QUEUED → RUNNING
   ├─ Build NodeContext (root objective, parent chain, sibling summaries, checker feedback)
   ├─ Apply persona routing (suggested_persona → persona_id)
   │
   ├─ ComplexityEstimator.estimate(objective, context, depth)
   │   ├─ score: 0.0 (trivial) → 1.0 (very complex)
   │   ├─ suggested_depth: 2-8
   │   └─ model_tier: "fast" | "standard" | "strong"
   │
   ├─ Adaptive depth: effective_max = min(max_depth, suggested_depth) if enabled
   │
   ├─ DividerService.divide(objective, context, complexity)
   │   ├─ LLM call → DividerResult (BASE_CASE or RECURSIVE_CASE)
   │   ├─ Multi-candidate: generate N at temp > 0, score, pick best
   │   └─ Parse → DividerServiceResult
   │
   ├── IF BASE_CASE ──────────────────────────────────────────┐
   │   │                                                       │
   │   ├─ _execute_base_case()                                 │
   │   │   ├─ Persona chain loop (or single persona):         │
   │   │   │   ├─ worker.execute(work_plan, persona, model)   │
   │   │   │   │   ├─ Step 1: LLM call → JSON result          │
   │   │   │   │   ├─ Step 2: LLM call → JSON result          │
   │   │   │   │   └─ ... (sliding context window)            │
   │   │   │   └─ If more personas: with_prior_output()       │
   │   │   │                                                   │
   │   │   ├─ Checker evaluation                               │
   │   │   │   ├─ Sandbox: generate tests → run code → score  │
   │   │   │   └─ Fallback: LLM evaluates output              │
   │   │   │                                                   │
   │   │   ├─ IF PASS → COMPLETED                              │
   │   │   ├─ IF FAIL → retry loop (max_retries)              │
   │   │   │   ├─ Inject checker feedback into context         │
   │   │   │   ├─ Re-execute worker                            │
   │   │   │   └─ Re-evaluate checker                          │
   │   │   └─ IF STILL FAIL → FAILED_CHECK or BLOCKED_HUMAN   │
   │   │                                                       │
   │   └─ _persist_usage() → flush tokens to repository       │
   │                                                           │
   ├── IF RECURSIVE_CASE ─────────────────────────────────────┐
   │   │                                                       │
   │   ├─ _execute_recursive_case()                            │
   │   │   ├─ Create child NodeStates (QUEUED)                │
   │   │   ├─ Create EdgeRecords (parent → child)             │
   │   │   ├─ Submit child execute_node() to thread pool      │
   │   │   └─ Wait for all children to reach terminal state   │
   │   │                                                       │
   │   ├─ _merge_children()                                    │
   │   │   ├─ Gather child outputs                             │
   │   │   ├─ MergerService.merge() → merged result           │
   │   │   └─ Checker evaluation on merged output             │
   │   │                                                       │
   │   └─ IF MERGE FAIL → retry with child feedback           │
   │                                                           │
   └── FINALIZE ──────────────────────────────────────────────┘
       ├─ record_node_completed / record_node_failed
       ├─ _persist_usage()
       └─ If all children terminal → parent can merge

3. Finalization
   │
   ├─ All nodes terminal?
   ├─ Compute run status (all COMPLETED → COMPLETED, any FAILED → FAILED)
   ├─ Emit RUN_STATUS_CHANGED, RUN_COMPLETED / RUN_FAILED
   └─ Result available at GET /api/runs/{run_id}/result
```

## Event Flow

```
DomainEvent published via event_stream.publish()
    │
    ├─ Persisted to repository (run_events table)
    ├─ Sequence number assigned (monotonic per run)
    └─ Fanout to all connected SSE clients
        │
        ├─ Client subscribes: GET /api/runs/{run_id}/events
        ├─ Replay from Last-Event-Id on reconnect
        └─ Events: run.created, node.status_changed, checker.completed,
                   token.usage_recorded, merge.completed, ...
```

## Token Flow

```
LLM call → LiteLLMClient → response.usage {prompt_tokens, completion_tokens}
    │
    └─ UsageTrackingClient accumulates
        │
        └─ _persist_usage() flushes to repository
            ├─ repository.increment_run_tokens(run_id, total)
            └─ Emit TOKEN_USAGE_RECORDED
                │
                └─ Frontend updates run.tokensUsed in Zustand store
```

## Checker Flow

```
CheckerService.evaluate(request)
    │
    ├─ Extract code from output (_extract_code)
    ├─ Extract language (_extract_language)
    │
    ├─ IF code found AND sandbox enabled:
    │   ├─ Generate tests (TestGeneratorService)
    │   │   ├─ Script-based: single test script
    │   │   └─ Case-based: multiple input/output pairs
    │   ├─ Run in sandbox (SubprocessBackend | EpicboxBackend)
    │   └─ Score: pass_ratio, violations, suggested_fix
    │
    └─ IF no code OR sandbox disabled:
        └─ LLMCheckerClient.evaluate() → verdict, reason, confidence
```
