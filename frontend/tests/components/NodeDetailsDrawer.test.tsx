// @ts-nocheck
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { NodeDetailsDrawer } from "../../src/components/NodeDetailsDrawer";
import type { Node } from "../../src/types/contracts";

const { apiMock, storeMock, MockApiError } = vi.hoisted(() => {
  class HoistedMockApiError extends Error {
    readonly status: number;

    constructor(message: string, status: number) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  return {
    apiMock: {
      retryNode: vi.fn(),
      editAndRetryNode: vi.fn(),
      skipNodeWithJustification: vi.fn(),
    },
    storeMock: {
      applyOptimisticIntervention: vi.fn(),
      applyInterventionResult: vi.fn(),
      rollbackOptimisticIntervention: vi.fn(),
    },
    MockApiError: HoistedMockApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: MockApiError,
  apiClient: apiMock,
}));

vi.mock("@/state/runStore", () => ({
  runStore: storeMock,
}));

vi.mock("@/components/InterventionPanel", () => ({
  InterventionPanel: ({
    isEligible,
    isSubmitting,
    errorMessage,
    onRetry,
    onEditAndRetry,
    onSkipWithJustification,
  }: {
    isEligible: boolean;
    isSubmitting: boolean;
    errorMessage?: string | null;
    onRetry: (note?: string) => Promise<void>;
    onEditAndRetry: (payload: {
      action: "edit_and_retry";
      editedObjective: string;
      editedContext?: string;
      note?: string;
    }) => Promise<void>;
    onSkipWithJustification: (justification: string) => Promise<void>;
  }) => (
    <div data-testid="intervention-panel-mock">
      <div data-testid="eligible-flag">{String(isEligible)}</div>
      <div data-testid="submitting-flag">{String(isSubmitting)}</div>
      {errorMessage ? <div role="alert">{errorMessage}</div> : null}

      <button type="button" onClick={() => onRetry("retry-note")}>
        Trigger retry
      </button>
      <button
        type="button"
        onClick={() =>
          onEditAndRetry({
            action: "edit_and_retry",
            editedObjective: "Edited objective",
            editedContext: "Edited context",
            note: "edit-note",
          })
        }
      >
        Trigger edit
      </button>
      <button type="button" onClick={() => onSkipWithJustification("skip-why")}>Trigger skip</button>
    </div>
  ),
}));

function makeNode(overrides: Partial<Node> = {}): Node {
  return {
    nodeId: "node-1",
    runId: "run-1",
    objective: "Analyze rollout risk",
    personaId: "reviewer",
    status: "blocked_human",
    ttftMs: 150,
    durationMs: 920,
    metadata: {
      context: { branch: "risk" },
      checker: { verdict: "fail", reason: "missing evidence" },
      interventions: {
        audit: [{ action: "retry", at: "2026-04-06T00:00:00.000Z", phase: "confirmed" }],
      },
    },
    output: { summary: "Pending fixes" },
    ...overrides,
  };
}

describe("NodeDetailsDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns null when closed or node is undefined", () => {
    const { rerender, container } = render(
      <NodeDetailsDrawer node={makeNode()} isOpen={false} onClose={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();

    rerender(<NodeDetailsDrawer node={undefined} isOpen onClose={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders key node details and audit data", () => {
    render(<NodeDetailsDrawer node={makeNode()} isOpen onClose={vi.fn()} />);

    expect(screen.getByRole("complementary", { name: "Node details" })).toBeTruthy();
    expect(screen.getByText("Node inspection")).toBeTruthy();
    expect(screen.getByText("node-1")).toBeTruthy();
    expect(screen.getByText("reviewer")).toBeTruthy();
    expect(screen.getByText("150 ms")).toBeTruthy();
    expect(screen.getByText("920 ms")).toBeTruthy();
    expect(screen.getByText("Analyze rollout risk")).toBeTruthy();
    expect(screen.getByText(/missing evidence/)).toBeTruthy();
    expect(screen.getByText("Intervention audit")).toBeTruthy();
  });

  it("applies optimistic + confirmed store updates on retry success", async () => {
    apiMock.retryNode.mockResolvedValueOnce({
      accepted: true,
      nodeStatus: "running",
      interventionId: "int-123",
    });

    render(<NodeDetailsDrawer node={makeNode()} isOpen onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Trigger retry" }));

    await waitFor(() => {
      expect(apiMock.retryNode).toHaveBeenCalledWith("run-1", "node-1", "retry-note");
      expect(storeMock.applyOptimisticIntervention).toHaveBeenCalledWith("node-1", {
        action: "retry",
        note: "retry-note",
        justification: undefined,
      });
      expect(storeMock.applyInterventionResult).toHaveBeenCalledWith("node-1", {
        action: "retry",
        interventionId: "int-123",
        accepted: true,
        nodeStatus: "running",
        note: "retry-note",
        justification: undefined,
      });
    });
  });

  it("rolls back optimistic state and surfaces ApiError message on failure", async () => {
    apiMock.retryNode.mockRejectedValueOnce(new MockApiError("Backend unavailable", 503));

    render(<NodeDetailsDrawer node={makeNode({ status: "failed" })} isOpen onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Trigger retry" }));

    await waitFor(() => {
      expect(storeMock.rollbackOptimisticIntervention).toHaveBeenCalledWith("node-1", {
        previousStatus: "failed",
      });
    });

    expect(screen.getByRole("alert").textContent).toContain("Backend unavailable");
  });
});
