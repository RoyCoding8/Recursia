import type { EventEnvelope, RunEvent } from "@/types/contracts";
import { ApiError } from "@/lib/api";
import { DEFAULT_BASE_URL } from "@/lib/config";

const SSE_EVENT_TYPES = [
  "run.created",
  "run.status_changed",
  "node.created",
  "node.status_changed",
  "node.token",
  "node.ttft_recorded",
  "checker.started",
  "checker.completed",
  "merge.started",
  "merge.completed",
  "node.blocked_human",
  "node.intervention_applied",
  "work.step_started",
  "work.step_completed",
  "run.completed",
  "run.failed",
] as const;

export interface SseClientOptions {
  baseUrl?: string;
  eventPath?: string;
  withCredentials?: boolean;
}

export interface SseCallbacks {
  onEvent: (event: RunEvent) => void;
  onError?: (error: unknown) => void;
  onOpen?: () => void;
}

export interface SseSubscription {
  close: () => void;
}


/**
 * Lightweight SSE wrapper for run event streams.
 * It keeps last sequence in-memory and drops duplicate/out-of-order events.
 */
export class RunEventsClient {
  private readonly baseUrl: string;
  private readonly eventPath: string;
  private readonly withCredentials: boolean;

  constructor(options: SseClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
    this.eventPath = options.eventPath ?? "/api/runs";
    this.withCredentials = options.withCredentials ?? false;
  }

  subscribe(runId: string, callbacks: SseCallbacks): SseSubscription {
    let lastSeq = -1;

    const streamPath = `${this.eventPath}/${encodeURIComponent(runId)}/events`;
    const streamUrl = new URL(streamPath, `${this.baseUrl}/`);

    const source = new EventSource(streamUrl.toString(), {
      withCredentials: this.withCredentials,
    });

    source.onopen = () => {
      callbacks.onOpen?.();
    };

    const handleMessage = (rawEvent: Event | MessageEvent<string>) => {
      const message = rawEvent as MessageEvent<string>;
      try {
        const parsed = mapEventEnvelope(JSON.parse(message.data));

        if (typeof parsed.seq !== "number") {
          return;
        }

        if (parsed.seq <= lastSeq) {
          return;
        }

        lastSeq = parsed.seq;
        callbacks.onEvent(parsed as RunEvent);
      } catch (error) {
        callbacks.onError?.(error);
      }
    };

    source.onmessage = handleMessage;

    if (typeof source.addEventListener === "function") {
      for (const eventType of SSE_EVENT_TYPES) {
        source.addEventListener(eventType, handleMessage as EventListener);
      }
    }

    source.onerror = (error) => {
      callbacks.onError?.(error);
    };

    return {
      close: () => {
        source.close();
      },
    };
  }
}

export const runEventsClient = new RunEventsClient();

function mapEventEnvelope(raw: unknown): EventEnvelope {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new ApiError("Invalid event envelope", 500, raw);
  }

  const envelope = raw as Record<string, unknown>;
  const payload = mapEventPayload(String(envelope.type ?? ""), envelope.payload);

  return {
    eventId: pickString(envelope, "event_id", "eventId"),
    runId: pickString(envelope, "run_id", "runId"),
    nodeId: pickOptionalString(envelope, "node_id", "nodeId"),
    seq: pickNumber(envelope, "seq"),
    type: pickString(envelope, "type") as EventEnvelope["type"],
    ts: pickString(envelope, "ts"),
    payload,
  };
}

function mapEventPayload(type: string, rawPayload: unknown): unknown {
  if (!rawPayload || typeof rawPayload !== "object" || Array.isArray(rawPayload)) {
    return rawPayload;
  }

  const payload = rawPayload as Record<string, unknown>;

  if (type === "node.ttft_recorded") {
    return {
      ...payload,
      ttftMs: pickNumber(payload, "ttft_ms", "ttftMs"),
    };
  }

  if (type === "node.created") {
    return {
      ...payload,
      parentNodeId: pickOptionalString(payload, "parent_node_id", "parent_id", "parentNodeId"),
      relation: pickOptionalString(payload, "relation") ?? "child",
    };
  }

  if (type === "node.intervention_applied") {
    return {
      ...payload,
      nodeStatus: pickOptionalString(payload, "node_status", "nodeStatus"),
    };
  }

  return payload;
}

function pickString(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string") {
      return value;
    }
  }
  throw new ApiError(`Event payload missing string field: ${keys.join(" | ")}`, 500, record);
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

function pickNumber(record: Record<string, unknown>, ...keys: string[]): number {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  throw new ApiError(`Event payload missing numeric field: ${keys.join(" | ")}`, 500, record);
}
