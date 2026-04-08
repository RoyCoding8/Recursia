"use client";

import { useEffect, useMemo, useState } from "react";

import { applyFileToDirectory, reviewExistingFile, type DirectoryHandleLike, type ExistingFileReview } from "@/lib/directoryReview";
import { extractFileProposals } from "@/lib/fileProposals";
import type { FileProposal, RunResultResponse } from "@/types/contracts";

interface ProposedFilesPanelProps {
  result?: RunResultResponse | null;
  selectedFolderName?: string;
  selectedFolderHandle?: DirectoryHandleLike;
}

type ApplyState = {
  status: "idle" | "applying" | "applied" | "error";
  message?: string;
};

function summarizeContent(content: string): string {
  const trimmed = content.trim();
  if (!trimmed) {
    return "Empty file";
  }

  const firstLine = trimmed.split("\n")[0] ?? "";
  return firstLine.length > 90 ? `${firstLine.slice(0, 90)}...` : firstLine;
}

function summarizeStatus(review: ExistingFileReview): string {
  switch (review.status) {
    case "existing":
      return "existing file";
    case "missing":
      return "new file";
    case "unsupported":
      return "comparison unavailable";
    case "error":
      return "read error";
    default:
      return "no target";
  }
}

export function ProposedFilesPanel({
  result,
  selectedFolderName,
  selectedFolderHandle,
}: ProposedFilesPanelProps) {
  const proposals = useMemo(() => extractFileProposals(result?.output), [result?.output]);
  const [selectedProposalIndex, setSelectedProposalIndex] = useState(0);
  const [existingReview, setExistingReview] = useState<ExistingFileReview>({
    status: "no_target",
    message: "Choose a target folder to compare staged content against existing files.",
  });
  const [applyStates, setApplyStates] = useState<Record<string, ApplyState>>({});
  const [applySummary, setApplySummary] = useState<string | null>(null);

  function proposalKey(proposal: FileProposal, index: number): string {
    return `${proposal.nodeId ?? "node"}-${proposal.stepIndex ?? index}-${proposal.path}`;
  }

  useEffect(() => {
    if (selectedProposalIndex >= proposals.length) {
      setSelectedProposalIndex(0);
    }
  }, [proposals.length, selectedProposalIndex]);

  const selectedProposal = proposals[selectedProposalIndex];
  const selectedProposalKey = selectedProposal ? proposalKey(selectedProposal, selectedProposalIndex) : undefined;

  useEffect(() => {
    let cancelled = false;

    if (!selectedProposal) {
      setExistingReview({
        status: "no_target",
        message: "No file proposal selected yet.",
      });
      return;
    }

    void reviewExistingFile(selectedFolderHandle, selectedProposal.path).then((review) => {
      if (!cancelled) {
        setExistingReview(review);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [selectedFolderHandle, selectedProposal]);

  const refreshSelectedReview = async () => {
    if (!selectedProposal) {
      return;
    }
    const review = await reviewExistingFile(selectedFolderHandle, selectedProposal.path);
    setExistingReview(review);
  };

  const applyProposal = async (proposal: FileProposal, index: number) => {
    const key = proposalKey(proposal, index);
    setApplySummary(null);
    setApplyStates((current) => ({
      ...current,
      [key]: { status: "applying", message: "Applying file..." },
    }));

    const outcome = await applyFileToDirectory(selectedFolderHandle, proposal.path, proposal.content);
    setApplyStates((current) => ({
      ...current,
      [key]: {
        status: outcome.status === "success" ? "applied" : "error",
        message: outcome.message,
      },
    }));
    setApplySummary(outcome.message);

    if (selectedProposalKey === key) {
      await refreshSelectedReview();
    }
  };

  const applyAll = async () => {
    setApplySummary(null);
    for (const [index, proposal] of proposals.entries()) {
      await applyProposal(proposal, index);
    }
  };

  function summarizeApplyState(state: ApplyState | undefined): string {
    switch (state?.status) {
      case "applying":
        return "applying";
      case "applied":
        return "applied";
      case "error":
        return "apply error";
      default:
        return "pending";
    }
  }

  return (
    <section className="panel proposedFilesPanel" aria-label="Proposed files panel">
      <div className="panelHeader">
        <div>
          <h2>Proposed Files</h2>
          <p className="resultPanelSubtle">
            Review staged changes, compare them with the selected folder, and apply only the files you approve.
          </p>
        </div>
        <span className="badge">{proposals.length} file{proposals.length === 1 ? "" : "s"}</span>
      </div>

      <p className="proposedFilesTarget">
        Apply target: {selectedFolderName ? <strong>{selectedFolderName}</strong> : "not selected yet"}
      </p>

      <div className="workspaceActions">
        <button
          type="button"
          className="buttonPrimary"
          onClick={() => {
            if (selectedProposal) {
              void applyProposal(selectedProposal, selectedProposalIndex);
            }
          }}
          disabled={!selectedProposal || !selectedFolderHandle}
        >
          Apply selected
        </button>
        <button
          type="button"
          className="buttonGhost"
          onClick={() => void applyAll()}
          disabled={proposals.length === 0 || !selectedFolderHandle}
        >
          Apply all
        </button>
      </div>

      {applySummary ? <p className="messageHint">{applySummary}</p> : null}

      {proposals.length === 0 ? (
        <p className="messageHint">
          No file proposals available yet. Completed runs that emit `file_proposals` will appear here.
        </p>
      ) : (
        <div className="proposalReviewLayout">
          <ul className="proposalList" aria-label="Proposed file list">
            {proposals.map((proposal, index) => (
              <li key={proposalKey(proposal, index)} className="proposalItem">
                <button
                  type="button"
                  className={`proposalSelectButton ${index === selectedProposalIndex ? "proposalSelectButtonActive" : ""}`}
                  onClick={() => setSelectedProposalIndex(index)}
                >
                  <div className="proposalTopRow">
                    <code>{proposal.path}</code>
                    <div className="proposalBadges">
                      {proposal.stepIndex !== undefined ? <span className="badge">step {proposal.stepIndex}</span> : null}
                      <span className="badge">{summarizeApplyState(applyStates[proposalKey(proposal, index)])}</span>
                    </div>
                  </div>
                  {proposal.sourceObjective ? (
                    <p className="proposalMeta">From: {proposal.sourceObjective}</p>
                  ) : null}
                  <p className="proposalPreview">{summarizeContent(proposal.content)}</p>
                  {applyStates[proposalKey(proposal, index)]?.message ? (
                    <p className="proposalMeta">{applyStates[proposalKey(proposal, index)]?.message}</p>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>

          {selectedProposal ? (
            <section className="proposalDiffCard" aria-label="Proposed file review">
              <div className="proposalTopRow">
                <code>{selectedProposal.path}</code>
                <span className="badge">{summarizeStatus(existingReview)}</span>
              </div>
              <p className="proposalMeta">{existingReview.message}</p>
              <div className="proposalDiffGrid">
                <div className="proposalDiffPane">
                  <h3>Current</h3>
                  <pre className="proposalCodeBlock">
                    {existingReview.status === "existing"
                      ? existingReview.content
                      : existingReview.status === "missing"
                        ? "File does not exist yet."
                        : existingReview.message}
                  </pre>
                </div>
                <div className="proposalDiffPane">
                  <h3>Proposed</h3>
                  <pre className="proposalCodeBlock">{selectedProposal.content}</pre>
                </div>
              </div>
            </section>
          ) : null}
        </div>
      )}
    </section>
  );
}
