"use client";

import { useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  type Edge,
  type Node as FlowNode,
  type NodeMouseHandler,
} from "reactflow";
import "reactflow/dist/style.css";

import type { GraphEdge, Node } from "@/types/contracts";
import { inferDecisionFromGraph } from "@/lib/decisionUtils";

interface GraphCanvasProps {
  nodes: Node[];
  edges: GraphEdge[];
  selectedNodeId?: string;
  onSelectNode: (nodeId: string) => void;
}

const statusClassMap: Record<Node["status"], string> = {
  queued: "rfNodeQueued",
  running: "rfNodeRunning",
  blocked_human: "rfNodeBlocked",
  completed: "rfNodeCompleted",
  failed: "rfNodeFailed",
  merged: "rfNodeMerged",
};

function computeTreeLayout(
  nodes: Node[],
  edges: GraphEdge[],
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  const childrenOf = new Map<string, string[]>();
  const nodeById = new Map<string, Node>();

  for (const node of nodes) {
    nodeById.set(node.nodeId, node);
  }
  for (const edge of edges) {
    if (edge.relation === "child") {
      const list = childrenOf.get(edge.source) ?? [];
      list.push(edge.target);
      childrenOf.set(edge.source, list);
    }
  }

  const root = nodes.find((n) => !n.parentNodeId && (n.depth ?? 0) === 0) ?? nodes[0];
  if (!root) return positions;

  const NODE_W = 280;
  const NODE_H = 140;
  let leafCounter = 0;

  function layout(nodeId: string, depth: number): { min: number; max: number } {
    const children = childrenOf.get(nodeId) ?? [];
    if (children.length === 0) {
      const y = leafCounter * NODE_H;
      leafCounter++;
      positions.set(nodeId, { x: depth * NODE_W, y });
      return { min: y, max: y };
    }
    let groupMin = Infinity;
    let groupMax = -Infinity;
    for (const childId of children) {
      const childNode = nodeById.get(childId);
      const childDepth = childNode?.depth ?? depth + 1;
      const range = layout(childId, childDepth);
      groupMin = Math.min(groupMin, range.min);
      groupMax = Math.max(groupMax, range.max);
    }
    const centerY = (groupMin + groupMax) / 2;
    positions.set(nodeId, { x: depth * NODE_W, y: centerY });
    return { min: groupMin, max: groupMax };
  }

  layout(root.nodeId, 0);

  // Position any orphan nodes not in the tree
  for (const node of nodes) {
    if (!positions.has(node.nodeId)) {
      const y = leafCounter * NODE_H;
      leafCounter++;
      positions.set(node.nodeId, { x: (node.depth ?? 0) * NODE_W, y });
    }
  }

  return positions;
}

export function GraphCanvas({ nodes, edges, selectedNodeId, onSelectNode }: GraphCanvasProps) {
  const flowNodes = useMemo<FlowNode[]>(() => {
    const treePositions = computeTreeLayout(nodes, edges);

    return nodes.map((node) => {
      const position = treePositions.get(node.nodeId) ?? { x: 0, y: 0 };
      const decision = inferDecisionFromGraph(node, edges);
      const isRecursive = decision.kind === "RECURSIVE_CASE";

      return {
        id: node.nodeId,
        data: {
          label: (
            <div className="rfNodeLabel">
              <div className="rfNodeTopRow">
                <strong>{node.personaId ?? "unit"}</strong>
                <span className={`rfCaseBadge ${isRecursive ? "rfCaseRecursive" : "rfCaseBase"}`}>
                  {isRecursive ? "Recursive container" : "Base work"}
                </span>
              </div>
              <p className="rfNodeObjective">{node.objective.slice(0, 120)}</p>
              <div className="rfNodeMetaRow">
                <small className="rfNodeStatus">
                  <span className="rfNodeStatusDot" aria-hidden="true" />
                  {node.status.replace("_", " ")}
                </small>
                <small className="rfNodeId">{node.nodeId.slice(0, 12)}</small>
              </div>
            </div>
          ),
        },
        position,
        className: ["rfNode", statusClassMap[node.status], selectedNodeId === node.nodeId ? "rfNodeSelected" : ""]
          .filter(Boolean)
          .join(" "),
      };
    });
  }, [edges, nodes, selectedNodeId]);

  const flowEdges = useMemo<Edge[]>(() => {
    return edges.map((edge) => ({
      id: `${edge.source}-${edge.target}-${edge.relation}`,
      source: edge.source,
      target: edge.target,
      animated: edge.relation === "merge_input",
      label: edge.relation,
      markerEnd: {
        type: MarkerType.ArrowClosed,
      },
      style: {
        strokeWidth: edge.relation === "merge_input" ? 2 : 1.5,
      },
    }));
  }, [edges]);

  const handleNodeClick: NodeMouseHandler = (_, node) => {
    onSelectNode(node.id);
  };

  return (
    <section className="panel canvasPanel" aria-label="Run graph">
      <div className="panelHeader">
        <h2>Execution Graph</h2>
        <span className="badge">Drag, pan, zoom, inspect</span>
      </div>

      <div className="canvasFrame">
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          onNodeClick={handleNodeClick}
          fitView
          minZoom={0.2}
          maxZoom={1.8}
          nodesDraggable
          panOnDrag
          panOnScroll
          selectionOnDrag={false}
          attributionPosition="bottom-left"
        >
          <Background gap={16} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </section>
  );
}
