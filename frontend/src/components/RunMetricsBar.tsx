"use client";

import type { Node, Run } from "@/types/contracts";

interface RunMetricsBarProps {
  run?: Run;
  nodes: Node[];
  streamConnected: boolean;
  streamError?: string;
  terminalReason?: string;
}

function formatMs(ms?: number): string {
  if (typeof ms !== "number") {
    return "—";
  }

  if (ms < 1000) {
    return `${ms} ms`;
  }

  return `${(ms / 1000).toFixed(2)} s`;
}

export function RunMetricsBar({ run, nodes, streamConnected, streamError, terminalReason }: RunMetricsBarProps) {
  const completed = nodes.filter((node) => node.status === "completed" || node.status === "merged").length;
  const running = nodes.filter((node) => node.status === "running").length;
  const blocked = nodes.filter((node) => node.status === "blocked_human").length;

  const ttftValues = nodes
    .map((node) => node.ttftMs)
    .filter((value): value is number => typeof value === "number");
  const avgTtft = ttftValues.length
    ? Math.round(ttftValues.reduce((sum, value) => sum + value, 0) / ttftValues.length)
    : undefined;

  const durationValues = nodes
    .map((node) => node.durationMs)
    .filter((value): value is number => typeof value === "number");
  const totalDuration = durationValues.length
    ? durationValues.reduce((sum, value) => sum + value, 0)
    : undefined;

  return (
    <section className="metricsBar" aria-label="Run metrics">
      <div className="metricItem">
        <span className="metricLabel">Run</span>
        <strong className="metricValue">{run?.status ?? "idle"}</strong>
      </div>

      <div className="metricItem">
        <span className="metricLabel">Nodes</span>
        <strong className="metricValue">{nodes.length}</strong>
      </div>

      <div className="metricItem">
        <span className="metricLabel">Completed</span>
        <strong className="metricValue">{completed}</strong>
      </div>

      <div className="metricItem">
        <span className="metricLabel">Running</span>
        <strong className="metricValue">{running}</strong>
      </div>

      <div className="metricItem">
        <span className="metricLabel">Blocked</span>
        <strong className="metricValue">{blocked}</strong>
      </div>

      <div className="metricItem">
        <span className="metricLabel">Avg TTFT</span>
        <strong className="metricValue">{formatMs(avgTtft)}</strong>
      </div>

      <div className="metricItem">
        <span className="metricLabel">Total Duration</span>
        <strong className="metricValue">{formatMs(totalDuration)}</strong>
      </div>

      <div className="metricItem streamMetric" data-connected={streamConnected}>
        <span className="metricLabel">Stream</span>
        <strong className="metricValue">{streamConnected ? "Connected" : "Reconnecting"}</strong>
        {streamError ? <span className="metricSubtle">{streamError}</span> : null}
      </div>

      {terminalReason ? (
        <div className="metricItem metricWide" data-terminal-reason="true">
          <span className="metricLabel">Terminal reason</span>
          <strong className="metricValue">{terminalReason}</strong>
        </div>
      ) : null}
    </section>
  );
}
