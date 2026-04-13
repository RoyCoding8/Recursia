import { normalizeNodeStatus } from "@/lib/statusUtils";
import type {
  CreateRunRequest,
  CreateRunResponse,
  EdgeRelation,
  GetRunResponse,
  InterventionRequest,
  InterventionResponse,
  Node,
  NodeStatus,
  PersonaSummary,
  Run,
  RunResultResponse,
  RunStatus,
} from "@/types/contracts";
import { DEFAULT_BASE_URL } from "@/lib/config";

export class ApiError extends Error {
  readonly status: number;
  readonly body?: unknown;

  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export interface ApiClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  headers?: HeadersInit;
}


export class ApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly defaultHeaders: HeadersInit;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.defaultHeaders = options.headers ?? {};
  }

  async createRun(payload: CreateRunRequest): Promise<CreateRunResponse> {
    const body = await this.request<unknown>("/api/runs", {
      method: "POST",
      body: JSON.stringify(mapCreateRunRequest(payload)),
    });

    return mapCreateRunResponse(body);
  }

  async listPersonas(): Promise<PersonaSummary[]> {
    const body = await this.request<unknown>("/api/personas", {
      method: "GET",
    });

    if (!Array.isArray(body)) {
      throw new ApiError("API response has invalid persona list shape", 500, body);
    }

    return body.map((entry) => mapPersonaSummary(asRecord(entry)));
  }

  async getRun(runId: string): Promise<GetRunResponse> {
    const body = await this.request<unknown>(`/api/runs/${encodeURIComponent(runId)}`, {
      method: "GET",
    });

    return mapGetRunResponse(body);
  }

  async getRunResult(runId: string): Promise<RunResultResponse> {
    const body = await this.request<unknown>(`/api/runs/${encodeURIComponent(runId)}/result`, {
      method: "GET",
    });

    return mapRunResultResponse(body);
  }

  async intervene(
    runId: string,
    nodeId: string,
    payload: InterventionRequest,
  ): Promise<InterventionResponse> {
    const body = await this.request<unknown>(
      `/api/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/interventions`,
      {
        method: "POST",
        body: JSON.stringify(mapInterventionRequest(payload)),
      },
    );

    return mapInterventionResponse(body);
  }

  async retryNode(runId: string, nodeId: string, note?: string): Promise<InterventionResponse> {
    return this.intervene(runId, nodeId, { action: "retry", note });
  }

  async editAndRetryNode(
    runId: string,
    nodeId: string,
    editedObjective: string,
    editedContext?: string,
    note?: string,
  ): Promise<InterventionResponse> {
    return this.intervene(runId, nodeId, {
      action: "edit_and_retry",
      editedObjective,
      editedContext,
      note,
    });
  }

  async skipNodeWithJustification(
    runId: string,
    nodeId: string,
    justification: string,
  ): Promise<InterventionResponse> {
    return this.intervene(runId, nodeId, {
      action: "skip_with_justification",
      justification,
    });
  }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const requestUrl = new URL(path, `${this.baseUrl}/`).toString();
    const response = await this.fetchImpl(requestUrl, {
      ...init,
      headers: {
        "content-type": "application/json",
        ...this.defaultHeaders,
        ...(init.headers ?? {}),
      },
    });

    const text = await response.text();
    const body = safeJsonParse(text);

    if (!response.ok) {
      throw new ApiError(
        `API request failed (${response.status}) for ${init.method ?? "GET"} ${path}`,
        response.status,
        body,
      );
    }

    return body as T;
  }
}

export const apiClient = new ApiClient();

function mapCreateRunResponse(body: unknown): CreateRunResponse {
  const input = asRecord(body);
  return {
    runId: pickString(input, "run_id", "runId"),
    rootNodeId: pickString(input, "root_node_id", "rootNodeId"),
    status: pickRunStatus(input, "status"),
  };
}

function mapCreateRunRequest(payload: CreateRunRequest): Record<string, unknown> {
  const result: Record<string, unknown> = {
    objective: payload.objective,
    base_persona_id: payload.basePersonaId,
  };

  if (payload.config) {
    const config: Record<string, unknown> = {};
    if (payload.config.checker) {
      const checker: Record<string, unknown> = {};
      if (payload.config.checker.enabled !== undefined) checker.enabled = payload.config.checker.enabled;
      if (payload.config.checker.nodeLevel !== undefined) checker.node_level = payload.config.checker.nodeLevel;
      if (payload.config.checker.mergeLevel !== undefined) checker.merge_level = payload.config.checker.mergeLevel;
      if (payload.config.checker.maxRetriesPerNode !== undefined) checker.max_retries_per_node = payload.config.checker.maxRetriesPerNode;
      if (payload.config.checker.onCheckFail !== undefined) checker.on_check_fail = payload.config.checker.onCheckFail;
      config.checker = checker;
    }
    if (payload.config.maxDepth !== undefined) config.max_depth = payload.config.maxDepth;
    if (payload.config.maxChildrenPerNode !== undefined) config.max_children_per_node = payload.config.maxChildrenPerNode;
    if (payload.config.stream) config.stream = payload.config.stream;
    
    result.config = config;
  }

  return result;
}

function mapGetRunResponse(body: unknown): GetRunResponse {
  const input = asRecord(body);
  const runPayload = asRecord(input.run);
  const nodesPayload = asArray(input.nodes);
  const edgesPayload = asArray(input.edges);

  return {
    run: mapRun(runPayload),
    nodes: nodesPayload.map((entry) => mapNode(asRecord(entry))),
    edges: edgesPayload.map((entry) => mapEdge(asRecord(entry))),
  };
}

function mapRunResultResponse(body: unknown): RunResultResponse {
  const input = asRecord(body);

  return {
    runId: pickString(input, "run_id", "runId"),
    status: pickRunStatus(input, "status"),
    output: input.output,
    error: pickOptionalString(input, "error"),
    validation: mapValidationResult(input.validation),
  };
}

function mapPersonaSummary(input: Record<string, unknown>): PersonaSummary {
  return {
    personaId: pickString(input, "persona_id", "personaId"),
    name: pickString(input, "name"),
    description: pickString(input, "description"),
  };
}

function mapRun(input: Record<string, unknown>): Run {
  return {
    runId: pickString(input, "run_id", "runId"),
    objective: pickString(input, "objective"),
    status: pickRunStatus(input, "status"),
    rootNodeId: pickString(input, "root_node_id", "rootNodeId"),
    createdAt: pickOptionalString(input, "created_at", "createdAt"),
    updatedAt: pickOptionalString(input, "updated_at", "updatedAt"),
  };
}

function mapNode(input: Record<string, unknown>): Node {
  const mapped: Node = {
    nodeId: pickString(input, "node_id", "nodeId"),
    runId: pickString(input, "run_id", "runId"),
    objective: pickString(input, "objective"),
    status: pickNodeStatus(input, "status"),
    parentNodeId: pickOptionalString(input, "parent_id", "parent_node_id", "parentNodeId"),
    personaId: pickOptionalString(input, "persona_id", "personaId"),
    depth: pickOptionalNumber(input, "depth"),
    ttftMs: pickOptionalNumber(input, "ttft_ms", "ttftMs"),
    durationMs: pickOptionalNumber(input, "duration_ms", "durationMs"),
    checkerFailureCount: pickOptionalNumber(
      input,
      "checker_failure_count",
      "checkerFailureCount",
    ),
  };

  const output = input.output;
  if (output !== undefined) {
    mapped.output = output;
  }

  const metadata = input.metadata;
  if (metadata && typeof metadata === "object" && !Array.isArray(metadata)) {
    mapped.metadata = metadata as Record<string, unknown>;
  }

  return mapped;
}

function mapEdge(input: Record<string, unknown>) {
  return {
    source: pickString(input, "source"),
    target: pickString(input, "target"),
    relation: pickEdgeRelation(input, "relation"),
  };
}

function mapInterventionRequest(payload: InterventionRequest): Record<string, unknown> {
  if (payload.action === "edit_and_retry") {
    return {
      action: payload.action,
      edited_objective: payload.editedObjective,
      edited_context: payload.editedContext,
      note: payload.note,
    };
  }

  return payload;
}

function mapInterventionResponse(body: unknown): InterventionResponse {
  const input = asRecord(body);
  return {
    accepted: pickBoolean(input, "accepted"),
    nodeStatus: pickNodeStatus(input, "node_status", "nodeStatus"),
    interventionId: pickString(input, "intervention_id", "interventionId"),
  };
}

function mapValidationResult(value: unknown): RunResultResponse["validation"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }

  const input = value as Record<string, unknown>;
  const verdict = pickString(input, "verdict");
  if (verdict !== "pass" && verdict !== "fail") {
    throw new ApiError(`API response contains unsupported validation verdict: ${verdict}`, 500, input);
  }

  const source = pickString(input, "source");
  if (source !== "checker") {
    throw new ApiError(`API response contains unsupported validation source: ${source}`, 500, input);
  }

  return {
    source,
    verdict,
    reason: pickString(input, "reason"),
    suggestedFix: pickOptionalString(input, "suggested_fix", "suggestedFix"),
    confidence: pickOptionalNumber(input, "confidence"),
    violations: Array.isArray(input.violations)
      ? input.violations.filter((entry): entry is string => typeof entry === "string")
      : undefined,
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiError("API response has invalid object shape", 500, value);
  }

  return value as Record<string, unknown>;
}

function asArray(value: unknown): unknown[] {
  if (!Array.isArray(value)) {
    throw new ApiError("API response has invalid array shape", 500, value);
  }

  return value;
}

function pickString(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.length > 0) {
      return value;
    }
  }

  throw new ApiError(`API response missing required string field: ${keys.join(" | ")}`, 500, record);
}

function pickOptionalString(record: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string") {
      return value;
    }
  }

  return undefined;
}

function pickOptionalNumber(record: Record<string, unknown>, ...keys: string[]): number | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }

  return undefined;
}

function pickBoolean(record: Record<string, unknown>, ...keys: string[]): boolean {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "boolean") {
      return value;
    }
  }

  throw new ApiError(`API response missing required boolean field: ${keys.join(" | ")}`, 500, record);
}

function pickRunStatus(record: Record<string, unknown>, ...keys: string[]): RunStatus {
  const value = pickString(record, ...keys);
  if (
    value === "queued" ||
    value === "running" ||
    value === "blocked_human" ||
    value === "completed" ||
    value === "failed" ||
    value === "canceled" ||
    value === "cancelled"
  ) {
    return value;
  }
  throw new ApiError(`API response contains unsupported run status: ${value}`, 500, record);
}

function pickNodeStatus(record: Record<string, unknown>, ...keys: string[]): NodeStatus {
  const value = pickString(record, ...keys);
  const normalized = normalizeNodeStatus(value, "queued");
  if (normalized === "queued" && value !== "queued" && !["running", "blocked_human", "completed", "failed", "merged", "waiting_check", "failed_check", "error"].includes(value)) {
    throw new ApiError(`API response contains unsupported node status: ${value}`, 500, record);
  }
  return normalized;
}

function pickEdgeRelation(record: Record<string, unknown>, ...keys: string[]): EdgeRelation {
  const value = pickString(record, ...keys);
  if (value === "child" || value === "merge_input") {
    return value;
  }
  throw new ApiError(`API response contains unsupported edge relation: ${value}`, 500, record);
}

function safeJsonParse(text: string): unknown {
  if (!text) {
    return undefined;
  }

  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
