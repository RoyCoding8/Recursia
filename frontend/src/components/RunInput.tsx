"use client";

import { FormEvent, useEffect, useState } from "react";

import type { PersonaSummary } from "@/types/contracts";

interface RunInputProps {
  onSubmit: (objective: string, basePersonaId?: string) => Promise<void> | void;
  isSubmitting?: boolean;
  personas?: PersonaSummary[];
  defaultPersonaId?: string;
  personasError?: string | null;
}

export function RunInput({
  onSubmit,
  isSubmitting = false,
  personas = [],
  defaultPersonaId,
  personasError,
}: RunInputProps) {
  const [objective, setObjective] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [selectedPersonaId, setSelectedPersonaId] = useState<string>(defaultPersonaId ?? "");

  useEffect(() => {
    if (defaultPersonaId) {
      setSelectedPersonaId(defaultPersonaId);
    }
  }, [defaultPersonaId]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const trimmed = objective.trim();
    if (!trimmed) {
      setError("Describe the root object before starting a run.");
      return;
    }

    setError(null);
    await onSubmit(trimmed, selectedPersonaId || undefined);
  };

  return (
    <form className="panel" onSubmit={handleSubmit} aria-label="Start recursive object run">
      <div className="panelHeader">
        <h2>Start run</h2>
        <span className="badge">Execution</span>
      </div>

      <label htmlFor="run-objective" className="label">
        Describe the root object and intended outcome
      </label>
      <textarea
        id="run-objective"
        className="textarea"
        value={objective}
        onChange={(event) => setObjective(event.target.value)}
        rows={4}
        placeholder="Example: Launch-week readiness: recursively split planning into engineering, design, QA, and release ops, then merge into one execution-ready plan."
        disabled={isSubmitting}
      />

      <label htmlFor="run-base-persona" className="label">
        Base persona
      </label>
      <select
        id="run-base-persona"
        className="selectInput"
        value={selectedPersonaId}
        onChange={(event) => setSelectedPersonaId(event.target.value)}
        disabled={isSubmitting || personas.length === 0}
      >
        {personas.length === 0 ? (
          <option value="">No personas found</option>
        ) : null}
        {personas.map((persona) => (
          <option key={persona.personaId} value={persona.personaId}>
            {persona.name}
          </option>
        ))}
      </select>

      {personas.length > 0 ? (
        <p className="messageHint">
          Base persona sets the root explicit persona. Child nodes can still route to more specific personas later.
        </p>
      ) : null}
      {personasError ? (
        <p className="messageError" role="alert">
          {personasError}
        </p>
      ) : null}

      {error ? (
        <p className="messageError" role="alert">
          {error}
        </p>
      ) : (
        <p className="messageHint">
          Execution starts immediately. Watch node status updates live, then inspect blocked or failed nodes for fast intervention.
        </p>
      )}

      <div className="actionsRow">
        <button type="submit" className="buttonPrimary" disabled={isSubmitting}>
          {isSubmitting ? "Starting…" : "Start Run"}
        </button>
      </div>
    </form>
  );
}
