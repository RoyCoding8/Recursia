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

type Listener = (state: RunState) => void;

const initialState: RunState = {
  run: undefined,
  nodesById: {},
  edges: [],
  eventLog: [],
  lastSeq: -1,
};

class RunStore {
  private state: RunState = initialState;
  private listeners = new Set<Listener>();

  getState(): RunState {
    return this.state;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  reset(next?: Partial<RunState>): void {
    this.state = {
      ...initialState,
      ...next,
      nodesById: next?.nodesById ?? {},
      edges: next?.edges ?? [],
      eventLog: next?.eventLog ?? [],
      lastSeq: next?.lastSeq ?? -1,
    };

    this.emit();
  }

  applyEvent(event: RunEvent): void {
    if (event.seq <= this.state.lastSeq) {
      return;
    }

    this.state = {
      ...this.state,
      lastSeq: event.seq,
      eventLog: [...this.state.eventLog, event],
    };

    switch (event.type) {
      case "run.created": {
        const payload = event.payload as { run?: Run };
        if (payload.run) {
          this.state = { ...this.state, run: payload.run };
        }
        break;
      }

      case "run.status_changed":
      case "run.completed":
      case "run.failed": {
        const payload = event.payload as { status?: Run["status"] };
        if (this.state.run && payload.status) {
          this.state = {
            ...this.state,
            run: {
              ...this.state.run,
              status: payload.status,
            },
          };
        }
        break;
      }

      case "node.created": {
        const payload = event.payload as { node?: Node; parentNodeId?: string; relation?: EdgeRelation };
        if (payload.node) {
          this.upsertNode(payload.node);
        }

        const parentId = payload.parentNodeId;
        const childId = payload.node?.nodeId;
        if (parentId && childId) {
          this.addEdge(parentId, childId, payload.relation ?? "child");
        }
        break;
      }

      case "node.status_changed": {
        if (!event.nodeId) break;
        const payload = event.payload as {
          status?: Node["status"];
          nodeKind?: Node["nodeKind"];
          durationMs?: number;
          ttftMs?: number;
          checkerFailureCount?: number;
          reason?: string;
          errorSource?: string;
        };
        const current = this.state.nodesById[event.nodeId];
        if (current && payload.status) {
          this.upsertNode({
            ...current,
            status: payload.status,
            nodeKind: payload.nodeKind ?? current.nodeKind,
            durationMs: typeof payload.durationMs === "number" ? payload.durationMs : current.durationMs,
            ttftMs: typeof payload.ttftMs === "number" ? payload.ttftMs : current.ttftMs,
            checkerFailureCount:
              typeof payload.checkerFailureCount === "number"
                ? payload.checkerFailureCount
                : current.checkerFailureCount,
            metadata: {
              ...(current.metadata ?? {}),
              ...(payload.reason ? { lastReason: payload.reason } : {}),
              ...(payload.errorSource ? { errorSource: payload.errorSource } : {}),
            },
          });
        }
        break;
      }

      case "node.ttft_recorded": {
        if (!event.nodeId) break;
        const payload = event.payload as { ttftMs?: number };
        const current = this.state.nodesById[event.nodeId];
        if (current && typeof payload.ttftMs === "number") {
          this.upsertNode({ ...current, ttftMs: payload.ttftMs });
        }
        break;
      }

      case "merge.completed": {
        if (!event.nodeId) break;
        const payload = event.payload as {
          unresolved_conflicts?: string[];
          unresolvedConflicts?: string[];
          has_unresolved_conflicts?: boolean;
          hasUnresolvedConflicts?: boolean;
        };
        const current = this.state.nodesById[event.nodeId];
        if (current) {
          const unresolved = payload.unresolvedConflicts ?? payload.unresolved_conflicts ?? [];
          const hasUnresolved =
            payload.hasUnresolvedConflicts ?? payload.has_unresolved_conflicts ?? unresolved.length > 0;
          this.upsertNode({
            ...current,
            status: hasUnresolved ? current.status : "merged",
            metadata: {
              ...(current.metadata ?? {}),
              mergeNotes: {
                unresolvedConflicts: unresolved,
                hasUnresolvedConflicts: hasUnresolved,
              },
            },
          });
        }
        break;
      }

      case "checker.completed": {
        if (!event.nodeId) break;
        const payload = event.payload as {
          verdict?: "pass" | "fail";
          reason?: string;
          suggestedFix?: string;
          suggested_fix?: string;
          confidence?: number;
          violations?: string[];
          consecutiveFailures?: number;
          consecutive_failures?: number;
        };
        const current = this.state.nodesById[event.nodeId];
        if (!current) break;

        const checker = {
          verdict: payload.verdict,
          reason: payload.reason,
          suggestedFix: payload.suggestedFix ?? payload.suggested_fix,
          confidence: payload.confidence,
          violations: payload.violations,
          consecutiveFailures:
            payload.consecutiveFailures ?? payload.consecutive_failures ?? current.checkerFailureCount,
        };

        this.upsertNode({
          ...current,
          checkerFailureCount:
            typeof checker.consecutiveFailures === "number"
              ? checker.consecutiveFailures
              : current.checkerFailureCount,
          metadata: {
            ...(current.metadata ?? {}),
            checker,
          },
        });
        break;
      }

      case "work.step_started": {
        if (!event.nodeId) break;
        const payload = event.payload as {
          stepIndex?: number;
          description?: string;
          totalSteps?: number;
        };
        const current = this.state.nodesById[event.nodeId];
        if (current) {
          this.upsertNode({
            ...current,
            metadata: {
              ...(current.metadata ?? {}),
              workProgress: {
                currentStep: payload.stepIndex,
                currentDescription: payload.description,
                totalSteps: payload.totalSteps,
                completedSteps: ((current.metadata as Record<string, unknown>)?.workProgress as Record<string, unknown>)?.completedSteps ?? 0,
              },
            },
          });
        }
        break;
      }

      case "work.step_completed": {
        if (!event.nodeId) break;
        const payload = event.payload as {
          stepIndex?: number;
          description?: string;
          totalSteps?: number;
          success?: boolean;
          error?: string;
        };
        const current = this.state.nodesById[event.nodeId];
        if (current) {
          const prevProgress = (current.metadata as Record<string, unknown>)?.workProgress as Record<string, unknown> | undefined;
          const prevCompleted = (typeof prevProgress?.completedSteps === "number" ? prevProgress.completedSteps : 0) as number;
          const prevStepLog = (Array.isArray(prevProgress?.stepLog) ? prevProgress.stepLog : []) as unknown[];
          this.upsertNode({
            ...current,
            metadata: {
              ...(current.metadata ?? {}),
              workProgress: {
                currentStep: payload.stepIndex,
                currentDescription: payload.description,
                totalSteps: payload.totalSteps,
                completedSteps: payload.success ? prevCompleted + 1 : prevCompleted,
                stepLog: [
                  ...prevStepLog,
                  {
                    step: payload.stepIndex,
                    description: payload.description,
                    success: payload.success,
                    error: payload.error,
                  },
                ],
              },
            },
          });
        }
        break;
      }

      case "node.blocked_human": {
        if (!event.nodeId) break;
        const current = this.state.nodesById[event.nodeId];
        if (current) {
          this.upsertNode({ ...current, status: "blocked_human" });
        }
        break;
      }

      case "node.intervention_applied": {
        if (!event.nodeId) break;
        const current = this.state.nodesById[event.nodeId];
        if (current) {
          const payload = event.payload as {
            action?: string;
            note?: string;
            justification?: string;
            nodeStatus?: string;
          };

          this.upsertNode({
            ...current,
            status: normalizeNodeStatus(payload.nodeStatus, "running"),
            metadata: this.mergeInterventionAudit(current.metadata, {
              action: payload.action ?? "unknown",
              note: payload.note,
              justification: payload.justification,
              nodeStatus: payload.nodeStatus,
              at: event.ts,
              phase: "confirmed",
            }),
          });
        }
        break;
      }

      default:
        break;
    }

    this.emit();
  }

  hydrate(run: Run, nodes: Node[], edges: GraphEdge[], lastSeq = -1): void {
    const nodesById = nodes.reduce<Record<string, Node>>((acc, node) => {
      acc[node.nodeId] = node;
      return acc;
    }, {});

    this.state = {
      ...this.state,
      run,
      nodesById,
      edges,
      lastSeq,
    };

    this.emit();
  }

  applyOptimisticIntervention(nodeId: string, payload: InterventionOptimisticPayload): void {
    const current = this.state.nodesById[nodeId];
    if (!current) {
      return;
    }

    const metadata = this.mergeInterventionAudit(current.metadata, {
      action: payload.action,
      note: payload.note,
      justification: payload.justification,
      at: new Date().toISOString(),
      phase: "optimistic",
    });

    this.upsertNode({
      ...current,
      status: "running",
      metadata,
    });

    this.emit();
  }

  applyInterventionResult(nodeId: string, payload: InterventionResultPayload): void {
    const current = this.state.nodesById[nodeId];
    if (!current) return;

    this.upsertNode({
      ...current,
      status: normalizeNodeStatus(payload.nodeStatus, current.status),
      metadata: this.mergeInterventionAudit(current.metadata, {
        action: payload.action,
        interventionId: payload.interventionId,
        accepted: payload.accepted,
        nodeStatus: payload.nodeStatus,
        note: payload.note,
        justification: payload.justification,
        at: new Date().toISOString(),
        phase: "confirmed",
      }),
    });
    this.emit();
  }

  rollbackOptimisticIntervention(nodeId: string, payload: InterventionRollbackPayload): void {
    const current = this.state.nodesById[nodeId];
    if (!current) {
      return;
    }

    this.upsertNode({
      ...current,
      status: payload.previousStatus,
    });

    this.emit();
  }

  private upsertNode(node: Node): void {
    this.state = {
      ...this.state,
      nodesById: {
        ...this.state.nodesById,
        [node.nodeId]: node,
      },
    };
  }

  private addEdge(source: string, target: string, relation: EdgeRelation): void {
    const exists = this.state.edges.some(
      (edge) => edge.source === source && edge.target === target && edge.relation === relation,
    );

    if (exists) {
      return;
    }

    this.state = {
      ...this.state,
      edges: [...this.state.edges, { source, target, relation }],
    };
  }

  private mergeInterventionAudit(
    metadata: Node["metadata"],
    entry: InterventionAuditEntry,
  ): Record<string, unknown> {
    const base = (metadata ?? {}) as Record<string, unknown>;
    const current = base.interventions as { audit?: InterventionAuditEntry[] } | undefined;
    const existingAudit = Array.isArray(current?.audit) ? current.audit : [];

    return {
      ...base,
      interventions: {
        ...(current || {}),
        lastAction: entry.action,
        lastUpdatedAt: entry.at,
        lastStatus: entry.nodeStatus,
        audit: [...existingAudit, entry],
      },
    };
  }

  private emit(): void {
    this.listeners.forEach((listener) => listener(this.state));
  }
}

export const runStore = new RunStore();
