"use client";

import { useEffect, useState } from "react";

import type { InterventionRequest, Node } from "@/types/contracts";

interface InterventionPanelProps {
  node: Node;
  isEligible: boolean;
  isSubmitting: boolean;
  errorMessage?: string | null;
  onRetry: (note?: string) => Promise<void>;
  onEditAndRetry: (
    payload: Extract<InterventionRequest, { action: "edit_and_retry" }>,
  ) => Promise<void>;
  onSkipWithJustification: (justification: string) => Promise<void>;
}

export function InterventionPanel({
  node,
  isEligible,
  isSubmitting,
  errorMessage,
  onRetry,
  onEditAndRetry,
  onSkipWithJustification,
}: InterventionPanelProps) {
  const [retryNote, setRetryNote] = useState("");
  const [editObjective, setEditObjective] = useState(node.objective);
  const [editContext, setEditContext] = useState("");
  const [editNote, setEditNote] = useState("");
  const [skipJustification, setSkipJustification] = useState("");
  const [validationMessage, setValidationMessage] = useState<string | null>(null);

  useEffect(() => {
    setEditObjective(node.objective);
    setValidationMessage(null);
  }, [node.nodeId, node.objective]);

  if (!isEligible) {
    return null;
  }

  const handleRetry = async () => {
    setValidationMessage(null);
    await onRetry(retryNote.trim() || undefined);
    setRetryNote("");
  };

  const handleEditAndRetry = async () => {
    const trimmedObjective = editObjective.trim();
    if (!trimmedObjective) {
      setValidationMessage("Edited objective is required.");
      return;
    }

    setValidationMessage(null);
    await onEditAndRetry({
      action: "edit_and_retry",
      editedObjective: trimmedObjective,
      editedContext: editContext.trim() || undefined,
      note: editNote.trim() || undefined,
    });
  };

  const handleSkip = async () => {
    const trimmed = skipJustification.trim();
    if (!trimmed) {
      setValidationMessage("Skip action requires a justification.");
      return;
    }

    setValidationMessage(null);
    await onSkipWithJustification(trimmed);
    setSkipJustification("");
  };

  return (
    <section className="detailSection" aria-label="Human intervention controls">
      <h4>Human intervention controls</h4>

      <p className="messageHint">
        Node is eligible for intervention ({node.status}). Choose retry, edit-and-retry, or skip with
        justification.
      </p>

      <div className="detailSection">
        <label className="label" htmlFor={`retry-note-${node.nodeId}`}>
          Retry note (optional)
        </label>
        <textarea
          id={`retry-note-${node.nodeId}`}
          className="textarea"
          rows={2}
          value={retryNote}
          onChange={(event) => setRetryNote(event.target.value)}
          placeholder="Why retrying as-is..."
          disabled={isSubmitting}
        />
        <div className="actionsRow">
          <button type="button" className="buttonPrimary" onClick={handleRetry} disabled={isSubmitting}>
            Retry
          </button>
        </div>
      </div>

      <div className="detailSection">
        <label className="label" htmlFor={`edit-objective-${node.nodeId}`}>
          Edited objective (required)
        </label>
        <textarea
          id={`edit-objective-${node.nodeId}`}
          className="textarea"
          rows={3}
          value={editObjective}
          onChange={(event) => setEditObjective(event.target.value)}
          disabled={isSubmitting}
        />

        <label className="label" htmlFor={`edit-context-${node.nodeId}`}>
          Edited context (optional)
        </label>
        <textarea
          id={`edit-context-${node.nodeId}`}
          className="textarea"
          rows={3}
          value={editContext}
          onChange={(event) => setEditContext(event.target.value)}
          disabled={isSubmitting}
        />

        <label className="label" htmlFor={`edit-note-${node.nodeId}`}>
          Edit note (optional)
        </label>
        <textarea
          id={`edit-note-${node.nodeId}`}
          className="textarea"
          rows={2}
          value={editNote}
          onChange={(event) => setEditNote(event.target.value)}
          placeholder="What changed and why..."
          disabled={isSubmitting}
        />

        <div className="actionsRow">
          <button
            type="button"
            className="buttonPrimary"
            onClick={handleEditAndRetry}
            disabled={isSubmitting}
          >
            Edit and retry
          </button>
        </div>
      </div>

      <div className="detailSection">
        <label className="label" htmlFor={`skip-justification-${node.nodeId}`}>
          Skip justification (required)
        </label>
        <textarea
          id={`skip-justification-${node.nodeId}`}
          className="textarea"
          rows={3}
          value={skipJustification}
          onChange={(event) => setSkipJustification(event.target.value)}
          placeholder="Provide rationale for skipping this node..."
          disabled={isSubmitting}
        />

        <div className="actionsRow">
          <button
            type="button"
            className="buttonGhost"
            onClick={handleSkip}
            disabled={isSubmitting}
          >
            Skip with justification
          </button>
        </div>
      </div>

      {validationMessage ? (
        <p role="alert" className="messageError">
          {validationMessage}
        </p>
      ) : null}

      {errorMessage ? (
        <p role="alert" className="messageError">
          {errorMessage}
        </p>
      ) : null}
    </section>
  );
}
