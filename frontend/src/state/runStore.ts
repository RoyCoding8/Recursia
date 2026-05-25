import { create } from "zustand";
import { normalizeNodeStatus } from "@/lib/statusUtils";
import type { EdgeRelation, GraphEdge, Node, Run, RunEvent } from "@/types/contracts";

type KnownNodeStatus = Node["status"];

interface InterventionAuditEntry {
  action: string;
  note?: string;
  justification?: string;
  interventionId?: string;
  accepted?: boolean;
  nodeStatus?: string;
  at: string;
  phase: "optimistic" | "confirmed";
}

interface InterventionOptimisticPayload {
  action: string;
  note?: string;
  justification?: string;
}

interface InterventionResultPayload {
  action: string;
  interventionId?: string;
  accepted?: boolean;
  nodeStatus?: string;
  note?: string;
  justification?: string;
}

interface InterventionRollbackPayload {
  previousStatus: KnownNodeStatus;
}

export interface RunState {
  run?: Run;
  nodesById: Record<string, Node>;
  edges: GraphEdge[];
  eventLog: RunEvent[];
  lastSeq: number;
}

interface RunActions {
  applyEvent: (event: RunEvent) => void;
  hydrate: (run: Run, nodes: Node[], edges: GraphEdge[], lastSeq?: number) => void;
  reset: (next?: Partial<RunState>) => void;
  applyOptimisticIntervention: (nodeId: string, payload: InterventionOptimisticPayload) => void;
  applyInterventionResult: (nodeId: string, payload: InterventionResultPayload) => void;
  rollbackOptimisticIntervention: (nodeId: string, payload: InterventionRollbackPayload) => void;
  removeNodeAndDescendants: (nodeId: string) => void;
}

const MAX_EVENT_LOG = 500;

const initialState: RunState = {
  run: undefined,
  nodesById: {},
  edges: [],
  eventLog: [],
  lastSeq: -1,
};

// Helpers (pure, no side effects)

function upsertNode(state: RunState, node: Node): RunState {
  return { ...state, nodesById: { ...state.nodesById, [node.nodeId]: node } };
}

function addEdge(state: RunState, source: string, target: string, relation: EdgeRelation): RunState {
  if (state.edges.some((e) => e.source === source && e.target === target && e.relation === relation)) return state;
  return { ...state, edges: [...state.edges, { source, target, relation }] };
}

function removeDescendants(state: RunState, parentId: string): RunState {
  const toRemove = new Set<string>();
  const queue = [parentId];
  while (queue.length) {
    const cur = queue.pop()!;
    for (const [id, node] of Object.entries(state.nodesById)) {
      if (node.parentNodeId === cur && !toRemove.has(id)) { toRemove.add(id); queue.push(id); }
    }
  }
  if (!toRemove.size) return state;
  const newNodes = { ...state.nodesById };
  for (const id of toRemove) delete newNodes[id];
  return {
    ...state,
    nodesById: newNodes,
    edges: state.edges.filter((e) => !toRemove.has(e.source) && !toRemove.has(e.target)),
  };
}

function buildMetadata(existing: Node["metadata"], clearProgress: boolean, extra: Record<string, unknown>): Record<string, unknown> {
  const meta: Record<string, unknown> = { ...(existing ?? {}), ...extra };
  if (clearProgress) delete meta.workProgress;
  return meta;
}

function stripWorkProgress(meta: Record<string, unknown>, clear: boolean): Record<string, unknown> {
  if (!clear) return meta;
  const copy = { ...meta };
  delete copy.workProgress;
  return copy;
}

function mergeInterventionAudit(metadata: Node["metadata"], entry: InterventionAuditEntry): Record<string, unknown> {
  const base = (metadata ?? {}) as Record<string, unknown>;
  const current = base.interventions as { audit?: InterventionAuditEntry[] } | undefined;
  const existingAudit = Array.isArray(current?.audit) ? current.audit : [];
  return {
    ...base,
    interventions: { ...(current || {}), lastAction: entry.action, lastUpdatedAt: entry.at, lastStatus: entry.nodeStatus, audit: [...existingAudit, entry] },
  };
}

// The store

export const useRunStore = create<RunState & RunActions>()((set, get) => ({
  ...initialState,

  reset: (next) => set({
    ...initialState, ...next,
    nodesById: next?.nodesById ?? {},
    edges: next?.edges ?? [],
    eventLog: next?.eventLog ?? [],
    lastSeq: next?.lastSeq ?? -1,
  }),

  hydrate: (run, nodes, edges, lastSeq = -1) => {
    const nodesById = nodes.reduce<Record<string, Node>>((acc, n) => { acc[n.nodeId] = n; return acc; }, {});
    set({ run, nodesById, edges, lastSeq });
  },

  applyEvent: (event) => {
    const state = get();
    if (event.seq <= state.lastSeq) return;

    // Deduplicate eventLog append, cap at MAX_EVENT_LOG
    const newLog = [...state.eventLog, event];
    const cappedLog = newLog.length > MAX_EVENT_LOG ? newLog.slice(newLog.length - MAX_EVENT_LOG) : newLog;
    const base = { ...state, lastSeq: event.seq, eventLog: cappedLog };

    switch (event.type) {
      case "run.created": {
        const p = event.payload as { run?: Run };
        if (p.run) set({ ...base, run: p.run });
        else set(base);
        return;
      }

      case "run.status_changed":
      case "run.completed":
      case "run.failed": {
        const p = event.payload as { status?: Run["status"] };
        if (base.run && p.status) set({ ...base, run: { ...base.run, status: p.status } });
        else set(base);
        return;
      }

      case "node.created": {
        const p = event.payload as { node?: Node; parentNodeId?: string; relation?: EdgeRelation };
        let s: RunState = base;
        if (p.node) s = upsertNode(s, p.node);
        if (p.parentNodeId && p.node?.nodeId) s = addEdge(s, p.parentNodeId, p.node.nodeId, p.relation ?? "child");
        set({ ...s });
        return;
      }

      case "node.status_changed": {
        if (!event.nodeId) { set(base); return; }
        const p = event.payload as { status?: Node["status"]; nodeKind?: Node["nodeKind"]; durationMs?: number; ttftMs?: number; checkerFailureCount?: number; reason?: string; errorSource?: string };
        const current = base.nodesById[event.nodeId];
        if (current && p.status) {
          const metadata = buildMetadata(current.metadata, p.status === "running", {
            ...(p.reason ? { lastReason: p.reason } : {}),
            ...(p.errorSource ? { errorSource: p.errorSource } : {}),
          });
          set(upsertNode(base, {
            ...current,
            status: p.status,
            nodeKind: p.nodeKind ?? current.nodeKind,
            durationMs: typeof p.durationMs === "number" ? p.durationMs : current.durationMs,
            ttftMs: typeof p.ttftMs === "number" ? p.ttftMs : current.ttftMs,
            checkerFailureCount: typeof p.checkerFailureCount === "number" ? p.checkerFailureCount : current.checkerFailureCount,
            metadata,
          }));
        } else set(base);
        return;
      }

      case "node.ttft_recorded": {
        if (!event.nodeId) { set(base); return; }
        const p = event.payload as { ttftMs?: number };
        const current = base.nodesById[event.nodeId];
        if (current && typeof p.ttftMs === "number") set(upsertNode(base, { ...current, ttftMs: p.ttftMs }));
        else set(base);
        return;
      }

      case "merge.completed": {
        if (!event.nodeId) { set(base); return; }
        const p = event.payload as { unresolved_conflicts?: string[]; unresolvedConflicts?: string[]; has_unresolved_conflicts?: boolean; hasUnresolvedConflicts?: boolean };
        const current = base.nodesById[event.nodeId];
        if (current) {
          const unresolved = p.unresolvedConflicts ?? p.unresolved_conflicts ?? [];
          const hasUnresolved = p.hasUnresolvedConflicts ?? p.has_unresolved_conflicts ?? unresolved.length > 0;
          set(upsertNode(base, {
            ...current,
            status: hasUnresolved ? current.status : "merged",
            metadata: { ...(current.metadata ?? {}), mergeNotes: { unresolvedConflicts: unresolved, hasUnresolvedConflicts: hasUnresolved } },
          }));
        } else set(base);
        return;
      }

      case "checker.completed": {
        if (!event.nodeId) { set(base); return; }
        const p = event.payload as { verdict?: "pass" | "fail"; reason?: string; suggestedFix?: string; suggested_fix?: string; confidence?: number; violations?: string[]; consecutiveFailures?: number; consecutive_failures?: number };
        const current = base.nodesById[event.nodeId];
        if (!current) { set(base); return; }
        const consecutiveFailures = p.consecutiveFailures ?? p.consecutive_failures ?? current.checkerFailureCount;
        set(upsertNode(base, {
          ...current,
          checkerFailureCount: typeof consecutiveFailures === "number" ? consecutiveFailures : current.checkerFailureCount,
          metadata: { ...(current.metadata ?? {}), checker: { verdict: p.verdict, reason: p.reason, suggestedFix: p.suggestedFix ?? p.suggested_fix, confidence: p.confidence, violations: p.violations, consecutiveFailures } },
        }));
        return;
      }

      case "work.step_started": {
        if (!event.nodeId) { set(base); return; }
        const p = event.payload as { stepIndex?: number; description?: string; totalSteps?: number };
        const current = base.nodesById[event.nodeId];
        if (current) {
          const prev = (current.metadata as Record<string, unknown>)?.workProgress as Record<string, unknown> | undefined;
          set(upsertNode(base, {
            ...current,
            metadata: { ...(current.metadata ?? {}), workProgress: { currentStep: p.stepIndex, currentDescription: p.description, totalSteps: p.totalSteps, completedSteps: prev?.completedSteps ?? 0 } },
          }));
        } else set(base);
        return;
      }

      case "work.step_completed": {
        if (!event.nodeId) { set(base); return; }
        const p = event.payload as { stepIndex?: number; description?: string; totalSteps?: number; success?: boolean; error?: string };
        const current = base.nodesById[event.nodeId];
        if (current) {
          const prev = (current.metadata as Record<string, unknown>)?.workProgress as Record<string, unknown> | undefined;
          const prevCompleted = (typeof prev?.completedSteps === "number" ? prev.completedSteps : 0) as number;
          const prevLog = (Array.isArray(prev?.stepLog) ? prev.stepLog : []) as unknown[];
          set(upsertNode(base, {
            ...current,
            metadata: {
              ...(current.metadata ?? {}),
              workProgress: {
                currentStep: p.stepIndex, currentDescription: p.description, totalSteps: p.totalSteps,
                completedSteps: p.success ? prevCompleted + 1 : prevCompleted,
                stepLog: [...prevLog, { step: p.stepIndex, description: p.description, success: p.success, error: p.error }],
              },
            },
          }));
        } else set(base);
        return;
      }

      case "node.blocked_human": {
        if (!event.nodeId) { set(base); return; }
        const current = base.nodesById[event.nodeId];
        if (current) set(upsertNode(base, { ...current, status: "blocked_human" }));
        else set(base);
        return;
      }

      case "node.subtree_pruned": {
        const p = event.payload as { parentNodeId?: string };
        if (p.parentNodeId) set(removeDescendants(base, p.parentNodeId));
        else set(base);
        return;
      }

      case "node.intervention_applied": {
        if (!event.nodeId) { set(base); return; }
        const current = base.nodesById[event.nodeId];
        if (current) {
          const p = event.payload as { action?: string; note?: string; justification?: string; nodeStatus?: string };
          const newStatus = normalizeNodeStatus(p.nodeStatus, "running");
          const merged = mergeInterventionAudit(current.metadata, {
            action: p.action ?? "unknown", note: p.note, justification: p.justification, nodeStatus: p.nodeStatus, at: event.ts, phase: "confirmed",
          });
          set(upsertNode(base, { ...current, status: newStatus, metadata: stripWorkProgress(merged, newStatus === "running") }));
        } else set(base);
        return;
      }

      case "token.usage_recorded": {
        if (!base.run) { set(base); return; }
        const p = event.payload as { total_tokens?: number; tokens_this_node?: number };
        const tokens = p.total_tokens ?? p.tokens_this_node ?? 0;
        set({ ...base, run: { ...base.run, tokensUsed: (base.run.tokensUsed ?? 0) + tokens } });
        return;
      }

      default:
        set(base);
    }
  },

  applyOptimisticIntervention: (nodeId, payload) => {
    const current = get().nodesById[nodeId];
    if (!current) return;
    set(upsertNode(get(), {
      ...current,
      status: "running",
      metadata: mergeInterventionAudit(current.metadata, { action: payload.action, note: payload.note, justification: payload.justification, at: new Date().toISOString(), phase: "optimistic" }),
    }));
  },

  applyInterventionResult: (nodeId, payload) => {
    const current = get().nodesById[nodeId];
    if (!current) return;
    set(upsertNode(get(), {
      ...current,
      status: normalizeNodeStatus(payload.nodeStatus, current.status),
      metadata: mergeInterventionAudit(current.metadata, { action: payload.action, interventionId: payload.interventionId, accepted: payload.accepted, nodeStatus: payload.nodeStatus, note: payload.note, justification: payload.justification, at: new Date().toISOString(), phase: "confirmed" }),
    }));
  },

  rollbackOptimisticIntervention: (nodeId, payload) => {
    const current = get().nodesById[nodeId];
    if (!current) return;
    set(upsertNode(get(), { ...current, status: payload.previousStatus }));
  },

  removeNodeAndDescendants: (nodeId) => {
    const s = removeDescendants(get(), nodeId);
    const newNodes = { ...s.nodesById };
    delete newNodes[nodeId];
    set({ ...s, nodesById: newNodes, edges: s.edges.filter((e) => e.source !== nodeId && e.target !== nodeId) });
  },
}));

// Backward-compatible singleton for direct method calls outside React
export const runStore = {
  getState: () => useRunStore.getState(),
  subscribe: (listener: () => void) => useRunStore.subscribe(listener),
  applyEvent: (event: RunEvent) => useRunStore.getState().applyEvent(event),
  hydrate: (run: Run, nodes: Node[], edges: GraphEdge[], lastSeq?: number) => useRunStore.getState().hydrate(run, nodes, edges, lastSeq),
  reset: (next?: Partial<RunState>) => useRunStore.getState().reset(next),
  removeNodeAndDescendants: (nodeId: string) => useRunStore.getState().removeNodeAndDescendants(nodeId),
  applyOptimisticIntervention: (nodeId: string, payload: InterventionOptimisticPayload) => useRunStore.getState().applyOptimisticIntervention(nodeId, payload),
  applyInterventionResult: (nodeId: string, payload: InterventionResultPayload) => useRunStore.getState().applyInterventionResult(nodeId, payload),
  rollbackOptimisticIntervention: (nodeId: string, payload: InterventionRollbackPayload) => useRunStore.getState().rollbackOptimisticIntervention(nodeId, payload),
};
