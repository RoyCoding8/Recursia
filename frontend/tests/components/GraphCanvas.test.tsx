// @ts-nocheck
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GraphCanvas } from "../../src/components/GraphCanvas";
import type { GraphEdge, Node } from "../../src/types/contracts";

type ReactFlowProps = {
  nodes: Array<{ id: string; className?: string }>;
  edges: Array<{ id: string; label?: string; animated?: boolean; style?: { strokeWidth?: number } }>;
  onNodeClick?: (_event: unknown, node: { id: string }) => void;
};

const reactFlowCapture: { lastProps?: ReactFlowProps } = {};

vi.mock("reactflow", () => {
  const ReactFlow = (props: ReactFlowProps) => {
    reactFlowCapture.lastProps = props;

    return (
      <div data-testid="react-flow-mock">
        <div data-testid="flow-nodes-count">{props.nodes.length}</div>
        <div data-testid="flow-edges-count">{props.edges.length}</div>
        {props.nodes.map((node) => (
          <button
            key={node.id}
            type="button"
            data-testid={`flow-node-${node.id}`}
            data-classname={node.className ?? ""}
            onClick={() => props.onNodeClick?.({}, { id: node.id })}
          >
            {node.id}
          </button>
        ))}
        {props.edges.map((edge) => (
          <div
            key={edge.id}
            data-testid={`flow-edge-${edge.id}`}
            data-label={edge.label ?? ""}
            data-animated={String(Boolean(edge.animated))}
            data-stroke-width={String(edge.style?.strokeWidth ?? "")}
          />
        ))}
      </div>
    );
  };

  const useNodesState = (initial: ReactFlowProps["nodes"]) => {
    const [nodes, setNodes] = React.useState(initial);
    return [nodes, setNodes, vi.fn()] as const;
  };

  return {
    __esModule: true,
    default: ReactFlow,
    useNodesState,
    Background: () => <div data-testid="flow-background" />,
    Controls: () => <div data-testid="flow-controls" />,
    MarkerType: {
      ArrowClosed: "arrowclosed",
    },
  };
});

describe("GraphCanvas", () => {
  const nodes: Node[] = [
    {
      nodeId: "node-root",
      runId: "run-1",
      objective: "Root planning objective",
      personaId: "orchestrator",
      status: "completed",
      depth: 0,
    },
    {
      nodeId: "node-work",
      runId: "run-1",
      parentNodeId: "node-root",
      objective: "Work node objective",
      personaId: "python_developer",
      status: "running",
      depth: 1,
    },
  ];

  const edges: GraphEdge[] = [
    { source: "node-root", target: "node-work", relation: "child" },
    { source: "node-work", target: "node-root", relation: "merge_input" },
  ];

  it("maps node statuses to classes and marks selected node", () => {
    render(
      <GraphCanvas
        nodes={nodes}
        edges={edges}
        selectedNodeId="node-work"
        onSelectNode={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Execution Graph" })).toBeTruthy();
    expect(screen.getByLabelText("Run graph")).toBeTruthy();

    const rootNode = screen.getByTestId("flow-node-node-root");
    const workNode = screen.getByTestId("flow-node-node-work");

    expect(rootNode.getAttribute("data-classname")).toContain("rfNodeCompleted");
    expect(workNode.getAttribute("data-classname")).toContain("rfNodeRunning");
    expect(workNode.getAttribute("data-classname")).toContain("rfNodeSelected");
  });

  it("calls onSelectNode when a rendered node is clicked", () => {
    const onSelectNode = vi.fn();

    render(
      <GraphCanvas
        nodes={nodes}
        edges={edges}
        selectedNodeId={undefined}
        onSelectNode={onSelectNode}
      />,
    );

    fireEvent.click(screen.getByTestId("flow-node-node-work"));
    expect(onSelectNode).toHaveBeenCalledTimes(1);
    expect(onSelectNode).toHaveBeenCalledWith("node-work");
  });

  it("maps merge_input edge as animated with stronger stroke", () => {
    render(
      <GraphCanvas
        nodes={nodes}
        edges={edges}
        selectedNodeId={undefined}
        onSelectNode={vi.fn()}
      />,
    );

    const childEdge = screen.getByTestId("flow-edge-node-root-node-work-child");
    const mergeEdge = screen.getByTestId("flow-edge-node-work-node-root-merge_input");

    expect(childEdge.getAttribute("data-animated")).toBe("false");
    expect(childEdge.getAttribute("data-stroke-width")).toBe("1.5");

    expect(mergeEdge.getAttribute("data-label")).toBe("merge_input");
    expect(mergeEdge.getAttribute("data-animated")).toBe("true");
    expect(mergeEdge.getAttribute("data-stroke-width")).toBe("2");

    expect(reactFlowCapture.lastProps?.edges).toHaveLength(2);
  });
});
