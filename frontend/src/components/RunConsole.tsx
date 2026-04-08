"use client";

import { useMemo } from "react";

import type { RunEvent } from "@/types/contracts";

interface RunConsoleProps {
  events: RunEvent[];
}

function summarize(event: RunEvent): string {
  const payload = (event.payload ?? {}) as Record<string, unknown>;

  switch (event.type) {
    case "run.failed":
      return String(payload.error ?? "run failed");
    case "run.completed":
      return "run completed";
    case "run.status_changed":
      return `run status -> ${String(payload.status ?? "unknown")}`;
    case "node.created":
      return `node created (${event.nodeId ?? "unknown"})`;
    case "node.status_changed":
      return `node status -> ${String(payload.status ?? "unknown")}`;
    case "checker.completed":
      return `checker ${String(payload.verdict ?? "unknown")}: ${String(payload.reason ?? "")}`;
    case "merge.completed":
      return "merge completed";
    case "work.step_started":
      return `step ${String(payload.stepIndex ?? "?")} started`;
    case "work.step_completed":
      return `step ${String(payload.stepIndex ?? "?")} ${payload.success ? "completed" : "failed"}`;
    case "node.blocked_human":
      return String(payload.reason ?? "blocked human");
    default:
      return "event received";
  }
}

export function RunConsole({ events }: RunConsoleProps) {
  const recent = useMemo(() => events.slice(-120), [events]);

  return (
    <section className="panel consolePanel" aria-label="Run event console">
      <div className="panelHeader">
        <h2>Runtime console</h2>
        <span className="badge">{recent.length} events</span>
      </div>

      <div className="consoleBody">
        {recent.length === 0 ? (
          <p className="consoleEmpty">No events yet. Start a run to stream logs.</p>
        ) : (
          recent.map((event) => (
            <div key={`${event.eventId}-${event.seq}`} className="consoleLine">
              <span className="consoleSeq">#{event.seq}</span>
              <span className="consoleType">{event.type}</span>
              <span className="consoleText">{summarize(event)}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
