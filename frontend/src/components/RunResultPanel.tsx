"use client";

import type { Run, RunResultResponse } from "@/types/contracts";

interface RunResultPanelProps {
  run?: Run;
  result?: RunResultResponse | null;
  isLoading: boolean;
  error?: string | null;
}

function snippetFromUnknown(value: unknown): string {
  if (value == null) {
    return "No final output available yet.";
  }

  if (typeof value === "string") {
    return value;
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function isTerminalStatus(status: Run["status"]): boolean {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "canceled" ||
    status === "cancelled"
  );
}

export function RunResultPanel({ run, result, isLoading, error }: RunResultPanelProps) {
  const status = result?.status ?? run?.status;
  const validation = result?.validation;
  let body = "Start a run to capture a final result payload here.";

  if (run && !isTerminalStatus(run.status)) {
    body = "Result pending. Final output will appear here once the run reaches a terminal state.";
  } else if (isLoading) {
    body = "Loading final output from the backend...";
  } else if (error) {
    body = error;
  } else if (result?.error) {
    body = result.error;
  } else if (result) {
    body = snippetFromUnknown(result.output);
  }

  return (
    <section className="panel resultPanel" aria-label="Final output panel">
      <div className="panelHeader">
        <div>
          <h2>Final output</h2>
          <p className="resultPanelSubtle">
            Canonical run result from <code>/api/runs/{`{id}`}/result</code>
          </p>
        </div>
        <span className="badge">{status ?? "idle"}</span>
      </div>

      {run ? (
        <div className="resultPanelMeta">
          <span>Run: {run.runId}</span>
          <span>Objective: {run.objective}</span>
        </div>
      ) : null}

      {validation ? (
        <section
          className={`resultValidationCard ${validation.verdict === "fail" ? "resultValidationFail" : "resultValidationPass"}`}
          aria-label="Validation result"
        >
          <div className="proposalTopRow">
            <strong>
              {validation.verdict === "fail" ? "Validation rejected this proposal" : "Validation passed"}
            </strong>
            <span className="badge">{validation.source}</span>
          </div>
          <p>{validation.reason}</p>
          {validation.suggestedFix ? (
            <p>
              Suggested fix: {validation.suggestedFix}
            </p>
          ) : null}
        </section>
      ) : null}

      <pre className="resultPanelBody">{body}</pre>
    </section>
  );
}
