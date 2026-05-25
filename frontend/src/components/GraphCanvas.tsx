"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  useNodesState,
  type Edge,
  type Node as FlowNode,
  type NodeMouseHandler,
} from "reactflow";
import "reactflow/dist/style.css";
import dagre from "dagre";

import type { GraphEdge, Node } from "@/types/contracts";
import { inferDecisionFromGraph } from "@/lib/decisionUtils";
import { apiClient } from "@/lib/api";

interface GraphCanvasProps {
  nodes: Node[];
  edges: GraphEdge[];
  selectedNodeId?: string;
  onSelectNode: (nodeId: string) => void;
  runId?: string;
  onDeleteNode?: (nodeId: string) => void;
}

const statusClassMap: Record<Node["status"], string> = {
  queued: "rfNodeQueued",
  running: "rfNodeRunning",
  blocked_human: "rfNodeBlocked",
  completed: "rfNodeCompleted",
  failed: "rfNodeFailed",
  merged: "rfNodeMerged",
};

const NODE_W = 280;
const NODE_H = 120;

function computeTreeLayout(nodes: Node[], edges: GraphEdge[]): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 120 });

  for (const node of nodes) g.setNode(node.nodeId, { width: NODE_W, height: NODE_H });
  for (const edge of edges) g.setEdge(edge.source, edge.target);

  dagre.layout(g);

  const positions = new Map<string, { x: number; y: number }>();
  for (const node of nodes) {
    const pos = g.node(node.nodeId);
    if (pos) positions.set(node.nodeId, { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 });
  }
  return positions;
}

export function GraphCanvas({ nodes, edges, selectedNodeId, onSelectNode, runId, onDeleteNode }: GraphCanvasProps) {
  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState([]);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; nodeId: string } | null>(null);

  useEffect(() => {
    const treePositions = computeTreeLayout(nodes, edges);

    setFlowNodes((prev) => {
      const existing = new Map(prev.map((n) => [n.id, n.position]));
      return nodes.map((node) => {
        const position = existing.get(node.nodeId) ?? treePositions.get(node.nodeId) ?? { x: 0, y: 0 };
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
    });
  }, [nodes, edges, selectedNodeId, setFlowNodes]);

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
    setContextMenu(null);
    onSelectNode(node.id);
  };

  const handleNodeContextMenu: NodeMouseHandler = useCallback((event, node) => {
    event.preventDefault();
    setContextMenu({ x: event.clientX, y: event.clientY, nodeId: node.id });
  }, []);

  const handlePaneClick = useCallback(() => {
    setContextMenu(null);
  }, []);

  const handleDeleteNode = useCallback(async () => {
    if (!contextMenu || !runId) return;
    const nodeId = contextMenu.nodeId;
    const childCount = edges.filter((e) => e.source === nodeId && e.relation === "child").length;
    const msg = childCount > 0
      ? `Delete this node and its ${childCount} direct children (and their descendants)?`
      : "Delete this node?";

    if (!window.confirm(msg)) {
      setContextMenu(null);
      return;
    }

    try {
      await apiClient.deleteNode(runId, nodeId);
      onDeleteNode?.(nodeId);
    } catch (err) {
      console.error("Failed to delete node:", err);
    }
    setContextMenu(null);
  }, [contextMenu, runId, edges, onDeleteNode]);

  const contextMenuNode = contextMenu ? nodes.find((n) => n.nodeId === contextMenu.nodeId) : null;
  const isRootNode = contextMenuNode ? !contextMenuNode.parentNodeId : false;

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
          onNodesChange={onNodesChange}
          onNodeClick={handleNodeClick}
          onNodeContextMenu={handleNodeContextMenu}
          onPaneClick={handlePaneClick}
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

        {contextMenu && (
          <div
            className="nodeContextMenu"
            style={{ top: contextMenu.y, left: contextMenu.x }}
          >
            <button
              disabled={isRootNode}
              onClick={handleDeleteNode}
              title={isRootNode ? "Cannot delete root node" : "Delete this node and its descendants"}
            >
              Delete node
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
