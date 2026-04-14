export type RunStatus =
  | "queued"
  | "running"
  | "blocked_human"
  | "completed"
  | "failed"
  | "canceled"
  | "cancelled";

export type NodeStatus =
  | "queued"
  | "running"
  | "blocked_human"
  | "completed"
  | "failed"
  | "merged";

export type EdgeRelation = "child" | "merge_input";

export interface RunConfig {
  checker?: {
    enabled?: boolean;
    nodeLevel?: boolean;
    mergeLevel?: boolean;
    maxRetriesPerNode?: number;
    onCheckFail?: "pause" | "auto_retry";
  };
  maxDepth: number;
  maxChildrenPerNode: number;
  stream: {
    mode: "sse" | "websocket";
  };
}

export interface Run {
  runId: string;
  objective: string;
  status: RunStatus;
  rootNodeId: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface Node {
  nodeId: string;
  runId: string;
  parentNodeId?: string;
  objective: string;
  personaId?: string;
  status: NodeStatus;
  nodeKind?: "work" | "divider";
  depth?: number;
  output?: unknown;
  ttftMs?: number;
  durationMs?: number;
  checkerFailureCount?: number;
  metadata?: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: EdgeRelation;
}

export interface CreateRunRequest {
  objective: string;
  config?: Partial<RunConfig>;
  basePersonaId?: string;
}

export interface CreateRunResponse {
  runId: string;
  status: RunStatus;
  rootNodeId: string;
}

export interface GetRunResponse {
  run: Run;
  nodes: Node[];
  edges: GraphEdge[];
}

export interface RunResultResponse {
  runId: string;
  status: RunStatus;
  output?: unknown;
  error?: string;
  validation?: {
    source: "checker";
    verdict: "pass" | "fail";
    reason: string;
    suggestedFix?: string;
    confidence?: number;
    violations?: string[];
  };
}

export interface PersonaSummary {
  personaId: string;
  name: string;
  description: string;
}

export interface FileProposal {
  path: string;
  content: string;
  stepIndex?: number;
  nodeId?: string;
  sourceObjective?: string;
  workspaceRoot?: string;
}

export type InterventionRequest =
  | { action: "retry"; note?: string }
  | {
      action: "edit_and_retry";
      editedObjective: string;
      editedContext?: string;
      note?: string;
    }
  | { action: "skip_with_justification"; justification: string };

export interface InterventionResponse {
  accepted: boolean;
  nodeStatus: NodeStatus | string;
  interventionId: string;
}

export type RunEventType =
  | "run.created"
  | "run.status_changed"
  | "node.created"
  | "node.status_changed"
  | "node.token"
  | "node.ttft_recorded"
  | "checker.started"
  | "checker.completed"
  | "merge.started"
  | "merge.completed"
  | "node.blocked_human"
  | "node.intervention_applied"
  | "node.subtree_pruned"
  | "work.step_started"
  | "work.step_completed"
  | "run.completed"
  | "run.failed";

export interface EventEnvelope<TPayload = unknown> {
  eventId: string;
  runId: string;
  nodeId?: string;
  seq: number;
  type: RunEventType;
  ts: string;
  payload: TPayload;
}

export type RunEvent =
  | EventEnvelope<{ run: Run }>
  | EventEnvelope<{ status: RunStatus; reason?: string }>
  | EventEnvelope<{ node: Node; parentNodeId?: string; relation?: EdgeRelation }>
  | EventEnvelope<{ status: NodeStatus; reason?: string }>
  | EventEnvelope<{ token: string; stream?: "stdout" | "stderr" | "output" }>
  | EventEnvelope<{ ttftMs: number }>
  | EventEnvelope<{ nodeId?: string; attempt?: number }>
  | EventEnvelope<{
      verdict: "pass" | "fail";
      reason: string;
      suggestedFix?: string;
      confidence?: number;
      violations?: string[];
    }>
  | EventEnvelope<{ parentNodeId?: string; childNodeIds?: string[] }>
  | EventEnvelope<{ mergedOutput?: unknown; unresolvedConflicts?: string[] }>
  | EventEnvelope<{ reason: string; retryCount?: number }>
  | EventEnvelope<{ action: InterventionRequest["action"]; note?: string }>
  | EventEnvelope<{ status: RunStatus; summary?: string }>
  | EventEnvelope<{ error: string; details?: unknown }>;
