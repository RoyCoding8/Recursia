import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkspaceTargetPanel } from "../../src/components/WorkspaceTargetPanel";

describe("WorkspaceTargetPanel", () => {
  it("shows the current backend fallback and empty target state", () => {
    render(
      <WorkspaceTargetPanel
        supportsDirectoryPicker
        isPicking={false}
        onPickFolder={vi.fn()}
        onClearFolder={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "Workspace target panel" })).toBeTruthy();
    expect(screen.getByText(/stages file proposals relative to/i)).toBeTruthy();
    expect(screen.getByText(/workspace\/\{run_id\}/)).toBeTruthy();
    expect(screen.getByText("No folder selected yet.")).toBeTruthy();
  });

  it("renders the selected folder name and enables clearing", () => {
    render(
      <WorkspaceTargetPanel
        selectedFolderName="client-app"
        supportsDirectoryPicker
        isPicking={false}
        onPickFolder={vi.fn()}
        onClearFolder={vi.fn()}
      />,
    );

    expect(screen.getByText("selected")).toBeTruthy();
    expect(screen.getByText("client-app")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Clear" })).not.toHaveAttribute("disabled");
  });

  it("shows an unsupported-browser hint and delegates button actions", () => {
    const onPickFolder = vi.fn();
    const onClearFolder = vi.fn();

    render(
      <WorkspaceTargetPanel
        selectedFolderName="draft"
        supportsDirectoryPicker={false}
        isPicking={false}
        error="Selection failed"
        onPickFolder={onPickFolder}
        onClearFolder={onClearFolder}
      />,
    );

    expect(screen.getByText(/directory picking is not available/i)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Selection failed");
    expect(screen.getByRole("button", { name: "Choose Folder" })).toHaveAttribute("disabled");

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onClearFolder).toHaveBeenCalledTimes(1);
    expect(onPickFolder).not.toHaveBeenCalled();
  });
});
