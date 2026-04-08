import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunResultPanel } from "../../src/components/RunResultPanel";
import type { Run, RunResultResponse } from "../../src/types/contracts";

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    runId: "run-123",
    objective: "Plan the rollout",
    status: "running",
    rootNodeId: "root-1",
    ...overrides,
  };
}

function makeResult(overrides: Partial<RunResultResponse> = {}): RunResultResponse {
  return {
    runId: "run-123",
    status: "completed",
    output: { summary: "Ship the feature" },
    ...overrides,
  };
}

describe("RunResultPanel", () => {
  it("shows a pending message while the run is still active", () => {
    render(<RunResultPanel run={makeRun()} isLoading={false} />);

    expect(screen.getByRole("region", { name: "Final output panel" })).toBeTruthy();
    expect(
      screen.getByText("Result pending. Final output will appear here once the run reaches a terminal state."),
    ).toBeTruthy();
  });

  it("renders the final output payload after completion", () => {
    render(
      <RunResultPanel
        run={makeRun({ status: "completed" })}
        result={makeResult()}
        isLoading={false}
      />,
    );

    expect(screen.getByText(/Ship the feature/)).toBeTruthy();
    expect(screen.getByText("completed")).toBeTruthy();
  });

  it("shows the terminal error when the run failed", () => {
    render(
      <RunResultPanel
        run={makeRun({ status: "failed" })}
        result={makeResult({ status: "failed", output: undefined, error: "merge step failed" })}
        isLoading={false}
      />,
    );

    expect(screen.getByText("merge step failed")).toBeTruthy();
    expect(screen.getByText("failed")).toBeTruthy();
  });

  it("shows checker validation separately from completed output", () => {
    render(
      <RunResultPanel
        run={makeRun({ status: "completed" })}
        result={makeResult({
          validation: {
            source: "checker",
            verdict: "fail",
            reason: "selector does not match the generated HTML",
            suggestedFix: "use a class selector that exists in the markup",
          },
        })}
        isLoading={false}
      />,
    );

    expect(screen.getByText("Validation rejected this proposal")).toBeTruthy();
    expect(screen.getByText("selector does not match the generated HTML")).toBeTruthy();
    expect(screen.getByText(/use a class selector that exists in the markup/i)).toBeTruthy();
    expect(screen.getByText(/Ship the feature/)).toBeTruthy();
    expect(screen.getByText("completed")).toBeTruthy();
  });
});
