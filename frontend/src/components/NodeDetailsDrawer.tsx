"use client";

import { useState } from "react";

import { ApiError, apiClient } from "@/lib/api";
import { inferDecision } from "@/lib/decisionUtils";
import { runStore } from "@/state/runStore";
import type { Node } from "@/types/contracts";
import { InterventionPanel } from "@/components/InterventionPanel";

interface NodeDetailsDrawerProps {
  node?: Node;
  isOpen: boolean;
  onClose: () => void;
}

function snippetFromUnknown(value: unknown): string {
  if (value == null) {
    return "No data available.";
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

export function NodeDetailsDrawer({ node, isOpen, onClose }: NodeDetailsDrawerProps) {
  const [isSubmittingIntervention, setIsSubmittingIntervention] = useState(false);
  const [interventionError, setInterventionError] = useState<string | null>(null);

  if (!isOpen || !node) {
    return null;
  }

  const outputSnippet = snippetFromUnknown(node.output);
  const contextSnippet = snippetFromUnknown(node.metadata?.context ?? node.metadata);
  const nodeMetadata = (node.metadata ?? {}) as Record<string, unknown>;
  const errorSource = typeof nodeMetadata.errorSource === "string" ? nodeMetadata.errorSource : undefined;
  const lastReason =
    typeof nodeMetadata.lastReason === "string" ? nodeMetadata.lastReason : undefined;
  const audit = (node.metadata as { interventions?: { audit?: unknown } } | undefined)?.interventions
    ?.audit;
  const interventionAudit = Array.isArray(audit) ? audit : [];
  const decision = inferDecision(node);

  const rawWorkProgress = (node.metadata as Record<string, unknown> | undefined)?.workProgress as
    | { currentStep?: number; currentDescription?: string; totalSteps?: number; completedSteps?: number; stepLog?: unknown[] }
    | undefined;
  const workProgress =
    rawWorkProgress && typeof rawWorkProgress.totalSteps === "number"
      ? {
          currentStep: rawWorkProgress.currentStep ?? 0,
          currentDescription: rawWorkProgress.currentDescription,
          totalSteps: rawWorkProgress.totalSteps,
          completedSteps: typeof rawWorkProgress.completedSteps === "number" ? rawWorkProgress.completedSteps : 0,
          stepLog: rawWorkProgress.stepLog,
        }
      : undefined;

  const isInterventionEligible = node.status === "blocked_human" || node.status === "failed";

  const performIntervention = async (
    action: "retry" | "edit_and_retry" | "skip_with_justification",
    request: () => Promise<{ accepted: boolean; nodeStatus: string; interventionId: string }>,
    optimistic: { note?: string; justification?: string },
  ) => {
    setInterventionError(null);
    setIsSubmittingIntervention(true);

    const previousStatus = node.status;

    try {
      runStore.applyOptimisticIntervention(node.nodeId, {
        action,
        note: optimistic.note,
        justification: optimistic.justification,
      });

      const response = await request();

      runStore.applyInterventionResult(node.nodeId, {
        action,
        interventionId: response.interventionId,
        accepted: response.accepted,
        nodeStatus: response.nodeStatus,
        note: optimistic.note,
        justification: optimistic.justification,
      });
    } catch (error) {
      runStore.rollbackOptimisticIntervention(node.nodeId, {
        previousStatus,
      });

      if (error instanceof ApiError) {
        setInterventionError(error.message);
      } else {
        setInterventionError("Failed to apply intervention.");
      }
    } finally {
      setIsSubmittingIntervention(false);
    }
  };

  const handleRetry = async (note?: string) => {
    await performIntervention(
      "retry",
      () => apiClient.retryNode(node.runId, node.nodeId, note),
      { note },
    );
  };

  const handleEditAndRetry = async (payload: {
    action: "edit_and_retry";
    editedObjective: string;
    editedContext?: string;
    note?: string;
  }) => {
    await performIntervention(
      "edit_and_retry",
      () =>
        apiClient.editAndRetryNode(
          node.runId,
          node.nodeId,
          payload.editedObjective,
          payload.editedContext,
          payload.note,
        ),
      { note: payload.note },
    );
  };

  const handleSkipWithJustification = async (justification: string) => {
    await performIntervention(
      "skip_with_justification",
      () => apiClient.skipNodeWithJustification(node.runId, node.nodeId, justification),
      { justification },
    );
  };

  return (
    <aside className="drawer" aria-label="Node details" role="complementary">
      <div className="drawerHeader">
        <div>
          <p className="drawerKicker">Node inspection</p>
          <h3 className="drawerTitle">{node.nodeId}</h3>
        </div>
        <button type="button" className="buttonGhost" onClick={onClose} aria-label="Close node details">
          Close
        </button>
      </div>

      <dl className="detailsGrid">
        <div>
          <dt>Status</dt>
          <dd>{node.status}</dd>
        </div>
        <div>
          <dt>Persona</dt>
          <dd>{node.personaId ?? "unassigned"}</dd>
        </div>
        <div>
          <dt>TTFT</dt>
          <dd>{typeof node.ttftMs === "number" ? `${node.ttftMs} ms` : "—"}</dd>
        </div>
        <div>
          <dt>Duration</dt>
          <dd>{typeof node.durationMs === "number" ? `${node.durationMs} ms` : "—"}</dd>
        </div>
      </dl>

      <section className="detailSection">
        <h4>Objective</h4>
        <p>{node.objective}</p>
      </section>

      <section className="detailSection decisionSection">
          <h4>Node type</h4>
          <p>
            <strong>{node.nodeKind === "work" ? "WORK (Base Case)" : node.nodeKind === "divider" ? "DIVIDER (Recursive)" : decision.kind}</strong>
            {" — "}
          {node.nodeKind === "work"
            ? "Leaf node executing a work plan."
            : node.nodeKind === "divider"
            ? "Recursive node that decomposes into children."
            : decision.reason}
          </p>
          {!node.nodeKind && <p className="decisionConfidence">Inferred classification (confidence: {decision.confidence})</p>}
        </section>

      {errorSource ? (
        <section className="detailSection">
          <h4>Error source</h4>
          <p>
            {errorSource === "llm_task_failure"
              ? "LLM failed to produce valid output for this task."
              : errorSource === "app_guardrail"
              ? "App guardrail triggered (depth/children limit)."
              : "Unknown error source."}
          </p>
          {lastReason ? <pre>{lastReason}</pre> : null}
        </section>
      ) : null}

      <section className="detailSection">
        <h4>Context snippet</h4>
        <pre>{contextSnippet}</pre>
      </section>

      <section className="detailSection">
        <h4>Output snippet</h4>
        <pre>{outputSnippet}</pre>
      </section>

      {workProgress ? (
        <section className="detailSection">
          <h4>Work progress</h4>
          <div className="workProgressBar">
            <div className="workProgressFill" style={{ width: `${Math.round((workProgress.completedSteps / Math.max(workProgress.totalSteps, 1)) * 100)}%` }} />
          </div>
          <p className="workProgressLabel">
            Step {workProgress.completedSteps}/{workProgress.totalSteps}
            {workProgress.currentDescription ? ` — ${workProgress.currentDescription}` : ""}
          </p>
          {Array.isArray(workProgress.stepLog) && workProgress.stepLog.length > 0 ? (
            <ul className="workStepLog">
              {(workProgress.stepLog as Array<{ step?: number; description?: string; success?: boolean; error?: string }>).map(
                (entry, i) => (
                  <li
                    key={i}
                    className={
                      entry.success === true
                        ? "stepSuccess"
                        : entry.success === false
                          ? "stepFailure"
                          : "stepPending"
                    }
                  >
                    {entry.success === true ? "\u2713" : entry.success === false ? "\u2717" : "\u2022"} Step {entry.step}: {entry.description}
                    {entry.error ? ` — ${entry.error}` : ""}
                  </li>
                ),
              )}
            </ul>
          ) : null}
        </section>
      ) : null}

      <section className="detailSection">
        <h4>Checker / merge notes</h4>
        <pre>{snippetFromUnknown(node.metadata?.checker ?? node.metadata?.mergeNotes ?? "No notes yet.")}</pre>
      </section>

      <InterventionPanel
        node={node}
        isEligible={isInterventionEligible}
        isSubmitting={isSubmittingIntervention}
        errorMessage={interventionError}
        onRetry={handleRetry}
        onEditAndRetry={handleEditAndRetry}
        onSkipWithJustification={handleSkipWithJustification}
      />

      {interventionAudit.length > 0 ? (
        <section className="detailSection">
          <h4>Intervention audit</h4>
          <pre>{snippetFromUnknown(interventionAudit)}</pre>
        </section>
      ) : null}
    </aside>
  );
}
