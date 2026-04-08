import type { GraphEdge, Node } from "@/types/contracts";

export type DecisionKind = "BASE_CASE" | "RECURSIVE_CASE";

export interface DecisionInterpretation {
  kind: DecisionKind;
  reason: string;
  confidence: "high" | "medium" | "low";
}

/**
 * Extract decision from node fields. Prefers authoritative nodeKind from backend.
 */
export function inferDecisionFromMetadata(node: Node): DecisionInterpretation | null {
  // Use authoritative nodeKind from backend if available
  const nodeKind = node.nodeKind;
  if (nodeKind === "work") {
    return { kind: "BASE_CASE", reason: "Backend classified this node as a work (base-case) node.", confidence: "high" };
  }
  if (nodeKind === "divider") {
    return { kind: "RECURSIVE_CASE", reason: "Backend classified this node as a divider (recursive) node.", confidence: "high" };
  }

  const metadata = node.metadata as Record<string, unknown> | undefined;

  const candidates = [
    metadata?.decision,
    metadata?.case,
    (metadata?.decomposition as { decision?: unknown } | undefined)?.decision,
    (metadata?.classification as { decision?: unknown } | undefined)?.decision,
    (metadata?.routing as { decision?: unknown } | undefined)?.decision,
  ];

  for (const value of candidates) {
    if (typeof value !== "string") {
      continue;
    }
    const normalized = value.toUpperCase().trim();
    if (normalized === "BASE_CASE") {
      return { kind: "BASE_CASE", reason: "Metadata explicitly labels this object as BASE_CASE.", confidence: "high" };
    }
    if (normalized === "RECURSIVE_CASE") {
      return { kind: "RECURSIVE_CASE", reason: "Metadata explicitly labels this object as RECURSIVE_CASE.", confidence: "high" };
    }
  }

  return null;
}

/**
 * Heuristic fallback when metadata has no explicit decision marker.
 */
function inferDecisionFromHeuristics(node: Node): DecisionInterpretation {
  const metadata = node.metadata as Record<string, unknown> | undefined;
  const metadataText = JSON.stringify(metadata ?? {}).toLowerCase();

  if (metadataText.includes("child") || metadataText.includes("decompos") || metadataText.includes("merge")) {
    return {
      kind: "RECURSIVE_CASE",
      reason: "Metadata references child/decomposition or merge patterns.",
      confidence: "medium",
    };
  }

  if (node.status === "merged") {
    return {
      kind: "RECURSIVE_CASE",
      reason: "Merged status typically means this object integrated outputs from subordinate units.",
      confidence: "medium",
    };
  }

  return {
    kind: "BASE_CASE",
    reason: "No explicit recursion markers detected; appears to be direct/base work.",
    confidence: "low",
  };
}

/**
 * Infer decision for a node using metadata first, then heuristics.
 * Used by NodeDetailsDrawer for detailed inspection.
 */
export function inferDecision(node: Node): DecisionInterpretation {
  return inferDecisionFromMetadata(node) ?? inferDecisionFromHeuristics(node);
}

/**
 * Infer decision using metadata + graph edge topology.
 * Used by GraphCanvas where edge structure is available.
 */
export function inferDecisionFromGraph(node: Node, edges: GraphEdge[]): DecisionInterpretation {
  const explicit = inferDecisionFromMetadata(node);
  if (explicit) {
    return explicit;
  }

  const outgoing = edges.filter((edge) => edge.source === node.nodeId);
  const incomingMergeInputs = edges.filter(
    (edge) => edge.target === node.nodeId && edge.relation === "merge_input",
  );
  const decompositionEdges = outgoing.filter((edge) => edge.relation !== "merge_input");

  if (decompositionEdges.length > 0) {
    return {
      kind: "RECURSIVE_CASE",
      reason: `has ${decompositionEdges.length} outgoing decomposition relation(s)`,
      confidence: "high",
    };
  }

  if (incomingMergeInputs.length >= 2) {
    return {
      kind: "RECURSIVE_CASE",
      reason: `collects ${incomingMergeInputs.length} merge inputs from child work`,
      confidence: "high",
    };
  }

  if (outgoing.length > 0) {
    return {
      kind: "BASE_CASE",
      reason: "only contributes direct work output (merge_input links)",
      confidence: "medium",
    };
  }

  // Fall back to heuristics when no edge info helps
  return inferDecisionFromHeuristics(node);
}
