import type { FileProposal } from "@/types/contracts";

type UnknownRecord = Record<string, unknown>;

interface ProposalContext {
  objective?: string;
  nodeId?: string;
}

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function toFileProposal(entry: unknown, context: ProposalContext): FileProposal | null {
  if (!isRecord(entry)) {
    return null;
  }

  const path = typeof entry.path === "string" ? entry.path : undefined;
  const content = typeof entry.content === "string" ? entry.content : undefined;
  if (!path || !content) {
    return null;
  }

  return {
    path,
    content,
    stepIndex: typeof entry.step_index === "number" ? entry.step_index : undefined,
    nodeId:
      typeof entry.node_id === "string"
        ? entry.node_id
        : typeof context.nodeId === "string"
          ? context.nodeId
          : undefined,
    sourceObjective: context.objective,
    workspaceRoot:
      typeof entry.workspace_root === "string" ? entry.workspace_root : undefined,
  };
}

function walk(value: unknown, proposals: FileProposal[], context: ProposalContext): void {
  if (Array.isArray(value)) {
    value.forEach((entry) => walk(entry, proposals, context));
    return;
  }

  if (!isRecord(value)) {
    return;
  }

  const nextContext: ProposalContext = {
    objective:
      typeof value.objective === "string" ? value.objective : context.objective,
    nodeId:
      typeof value.node_id === "string"
        ? value.node_id
        : typeof value.nodeId === "string"
          ? value.nodeId
          : context.nodeId,
  };

  if (Array.isArray(value.file_proposals)) {
    value.file_proposals.forEach((entry) => {
      const proposal = toFileProposal(entry, nextContext);
      if (proposal) {
        proposals.push(proposal);
      }
    });
  }

  Object.entries(value).forEach(([key, child]) => {
    if (key === "file_proposals") {
      return;
    }
    walk(child, proposals, nextContext);
  });
}

export function extractFileProposals(value: unknown): FileProposal[] {
  const proposals: FileProposal[] = [];
  walk(value, proposals, {});

  const deduped = new Map<string, FileProposal>();
  proposals.forEach((proposal) => {
    const key = [
      proposal.nodeId ?? "",
      proposal.sourceObjective ?? "",
      proposal.stepIndex ?? "",
      proposal.path,
    ].join("::");
    if (!deduped.has(key)) {
      deduped.set(key, proposal);
    }
  });

  return Array.from(deduped.values());
}
