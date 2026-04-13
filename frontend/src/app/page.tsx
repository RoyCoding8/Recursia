"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { GraphCanvas } from "@/components/GraphCanvas";
import { NodeDetailsDrawer } from "@/components/NodeDetailsDrawer";
import { ProposedFilesPanel } from "@/components/ProposedFilesPanel";
import { RunConsole } from "@/components/RunConsole";
import { RunInput } from "@/components/RunInput";
import { RunMetricsBar } from "@/components/RunMetricsBar";
import { RunResultPanel } from "@/components/RunResultPanel";
import { WorkspaceTargetPanel } from "@/components/WorkspaceTargetPanel";
import { apiClient, ApiError } from "@/lib/api";
import { DEFAULT_BASE_URL } from "@/lib/config";
import type { DirectoryHandleLike } from "@/lib/directoryReview";
import { runEventsClient, type SseSubscription } from "@/lib/events";
import { runStore } from "@/state/runStore";
import type { PersonaSummary, RunResultResponse, RunStatus } from "@/types/contracts";

interface DirectoryPickerWindow extends Window {
  showDirectoryPicker?: () => Promise<DirectoryHandleLike>;
}

function isTerminalRunStatus(status: RunStatus): boolean {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "canceled" ||
    status === "cancelled"
  );
}

export default function MissionControlPage() {
  const state = useSyncExternalStore(
    runStore.subscribe.bind(runStore),
    runStore.getState.bind(runStore),
    runStore.getState.bind(runStore),
  );
  const [selectedNodeId, setSelectedNodeId] = useState<string | undefined>(undefined);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [streamConnected, setStreamConnected] = useState(false);
  const [streamError, setStreamError] = useState<string | undefined>(undefined);
  const [runResult, setRunResult] = useState<RunResultResponse | null>(null);
  const [runResultError, setRunResultError] = useState<string | null>(null);
  const [isRunResultLoading, setIsRunResultLoading] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [selectedFolderName, setSelectedFolderName] = useState<string | undefined>(undefined);
  const [selectedFolderHandle, setSelectedFolderHandle] = useState<DirectoryHandleLike | undefined>(undefined);
  const [supportsDirectoryPicker, setSupportsDirectoryPicker] = useState(false);
  const [isPickingFolder, setIsPickingFolder] = useState(false);
  const [folderPickerError, setFolderPickerError] = useState<string | null>(null);
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [personasError, setPersonasError] = useState<string | null>(null);
  const activeStream = useRef<SseSubscription | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const root = document.documentElement;
    const saved = window.localStorage.getItem("cm-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const initial = saved === "dark" || saved === "light" ? saved : prefersDark ? "dark" : "light";

    root.setAttribute("data-theme", initial);
    setTheme(initial);
    setSupportsDirectoryPicker(typeof (window as DirectoryPickerWindow).showDirectoryPicker === "function");
  }, []);

  useEffect(() => {
    let cancelled = false;

    void apiClient
      .listPersonas()
      .then((profiles) => {
        if (!cancelled) {
          setPersonas(profiles);
          setPersonasError(null);
        }
      })
      .catch((loadError: unknown) => {
        if (cancelled) {
          return;
        }
        setPersonas([]);
        setPersonasError(
          loadError instanceof Error
            ? `Failed to load personas: ${loadError.message}`
            : `Failed to load personas: ${String(loadError)}`,
        );
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const toggleTheme = () => {
    const nextTheme = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", nextTheme);
    window.localStorage.setItem("cm-theme", nextTheme);
    setTheme(nextTheme);
  };

  const nodes = useMemo(() => Object.values(state.nodesById), [state.nodesById]);
  const selectedNode = selectedNodeId ? state.nodesById[selectedNodeId] : undefined;
  const terminalReason = useMemo(() => {
    if (!state.run || !isTerminalRunStatus(state.run.status)) {
      return undefined;
    }

    if (runResult?.error && runResult.error.trim().length > 0) {
      return runResult.error;
    }

    const failEvent = [...state.eventLog]
      .reverse()
      .find((event) => event.type === "run.failed") as
      | { payload?: { error?: unknown } }
      | undefined;

    const payloadError = failEvent?.payload?.error;
    if (typeof payloadError === "string" && payloadError.trim().length > 0) {
      return payloadError;
    }

    return state.run.status === "completed"
      ? "Run completed successfully."
      : undefined;
  }, [runResult?.error, state.eventLog, state.run]);

  useEffect(() => {
    return () => {
      activeStream.current?.close();
    };
  }, []);

  useEffect(() => {
    if (!state.run?.runId) {
      setRunResult(null);
      setRunResultError(null);
      setIsRunResultLoading(false);
      return;
    }

    setRunResult((current) => (current?.runId === state.run?.runId ? current : null));
    setRunResultError(null);
    setIsRunResultLoading(false);
  }, [state.run?.runId]);

  useEffect(() => {
    const run = state.run;
    if (!run || !isTerminalRunStatus(run.status)) {
      setIsRunResultLoading(false);
      return;
    }

    if (runResult?.runId === run.runId) {
      return;
    }

    let isCancelled = false;

    setIsRunResultLoading(true);
    setRunResultError(null);

    void apiClient
      .getRunResult(run.runId)
      .then((result) => {
        if (isCancelled) {
          return;
        }
        setRunResult(result);
      })
      .catch((error: unknown) => {
        if (isCancelled) {
          return;
        }

        if (error instanceof ApiError) {
          setRunResultError(error.message);
          return;
        }

        setRunResultError(
          `Failed to load final output: ${error instanceof Error ? error.message : String(error)}`,
        );
      })
      .finally(() => {
        if (!isCancelled) {
          setIsRunResultLoading(false);
        }
      });

    return () => {
      isCancelled = true;
    };
  }, [runResult?.runId, state.run]);

  const startStream = (runId: string) => {
    activeStream.current?.close();
    setStreamConnected(false);
    setStreamError(undefined);

    activeStream.current = runEventsClient.subscribe(runId, {
      onOpen: () => {
        setStreamConnected(true);
        setStreamError(undefined);
      },
      onEvent: (event) => {
        setStreamConnected(true);
        runStore.applyEvent(event);
      },
      onError: (error) => {
        setStreamConnected(false);
        const message = error instanceof Error ? error.message : "Event stream interrupted";
        setStreamError(message);
      },
    });
  };

  const handlePickFolder = async () => {
    if (typeof window === "undefined") {
      return;
    }

    const pickerWindow = window as DirectoryPickerWindow;
    if (typeof pickerWindow.showDirectoryPicker !== "function") {
      setFolderPickerError("Directory selection is not supported in this browser environment.");
      return;
    }

    try {
      setFolderPickerError(null);
      setIsPickingFolder(true);
      const handle = await pickerWindow.showDirectoryPicker();
      setSelectedFolderHandle(handle);
      setSelectedFolderName(handle.name);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (message.toLowerCase().includes("abort")) {
        return;
      }
      setFolderPickerError(`Failed to choose folder: ${message}`);
    } finally {
      setIsPickingFolder(false);
    }
  };

  const handleClearFolder = () => {
    setSelectedFolderHandle(undefined);
    setSelectedFolderName(undefined);
    setFolderPickerError(null);
  };

  const defaultPersonaId = useMemo(() => {
    if (personas.length === 0) {
      return undefined;
    }
    return personas.find((persona) => persona.personaId === "python_developer")?.personaId ?? personas[0]?.personaId;
  }, [personas]);

  const handleStartRun = async (objective: string, basePersonaId?: string, config?: { checker?: { onCheckFail?: "pause" | "auto_retry" } }) => {
    try {
      setSubmitError(null);
      setIsSubmitting(true);
      setRunResult(null);
      setRunResultError(null);
      setIsRunResultLoading(false);

      const created = await apiClient.createRun({ objective, basePersonaId, config });
      startStream(created.runId);
      const snapshot = await apiClient.getRun(created.runId);

      runStore.hydrate(snapshot.run, snapshot.nodes, snapshot.edges);
      setSelectedNodeId(snapshot.run.rootNodeId);
    } catch (error) {
      if (error instanceof ApiError) {
        setSubmitError(`${error.message}`);
      } else if (error instanceof TypeError && String(error).includes("fetch")) {
        setSubmitError(
          `Cannot reach backend at ${DEFAULT_BASE_URL} (${error.message}).`,
        );
      } else {
        setSubmitError(`Failed to start run: ${error instanceof Error ? error.message : String(error)}`);
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="missionControlPage">
      <header className="hero">
        <div className="heroTopRow">
          <h1>Recursia Mission Control</h1>
          <button
            type="button"
            className="buttonGhost themeToggle"
            onClick={toggleTheme}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            <span aria-hidden="true">{theme === "dark" ? "☀️" : "🌙"}</span>
          </button>
        </div>
        <p>Diagnose recursive workflow failures fast, intervene on blocked nodes, and track execution health in real time.</p>
      </header>

      <WorkspaceTargetPanel
        selectedFolderName={selectedFolderName}
        supportsDirectoryPicker={supportsDirectoryPicker}
        isPicking={isPickingFolder}
        error={folderPickerError}
        onPickFolder={handlePickFolder}
        onClearFolder={handleClearFolder}
      />

      <RunInput
        onSubmit={handleStartRun}
        isSubmitting={isSubmitting}
        personas={personas}
        defaultPersonaId={defaultPersonaId}
        personasError={personasError}
      />

      {submitError ? (
        <p className="bannerError" role="alert">
          {submitError}
        </p>
      ) : null}

      <RunMetricsBar
        run={state.run}
        nodes={nodes}
        streamConnected={streamConnected}
        streamError={streamError}
        terminalReason={terminalReason}
      />

      <RunResultPanel
        run={state.run}
        result={runResult}
        isLoading={isRunResultLoading}
        error={runResultError}
      />

      <ProposedFilesPanel
        result={runResult}
        selectedFolderName={selectedFolderName}
        selectedFolderHandle={selectedFolderHandle}
      />

      <RunConsole events={state.eventLog} />

      <div className="mainGrid">
        <GraphCanvas
          nodes={nodes}
          edges={state.edges}
          selectedNodeId={selectedNodeId}
          onSelectNode={setSelectedNodeId}
        />

        <NodeDetailsDrawer
          node={selectedNode}
          isOpen={Boolean(selectedNode)}
          onClose={() => setSelectedNodeId(undefined)}
        />
      </div>
    </main>
  );
}
