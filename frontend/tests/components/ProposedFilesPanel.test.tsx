import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProposedFilesPanel } from "../../src/components/ProposedFilesPanel";
import type { RunResultResponse } from "../../src/types/contracts";
import type { DirectoryHandleLike } from "../../src/lib/directoryReview";

function makeResult(output: unknown): RunResultResponse {
  return {
    runId: "run-123",
    status: "completed",
    output,
  };
}

describe("ProposedFilesPanel", () => {
  it("shows an empty state when no proposals exist", () => {
    render(<ProposedFilesPanel result={makeResult({ summary: "done" })} />);

    expect(screen.getByRole("region", { name: "Proposed files panel" })).toBeTruthy();
    expect(screen.getByText(/No file proposals available yet/i)).toBeTruthy();
  });

  it("renders proposals extracted from nested run output", () => {
    render(
      <ProposedFilesPanel
        selectedFolderName="client-app"
        result={makeResult({
          parent_objective: "Ship app",
          children: [
            {
              alias: "child_1",
              node_id: "node-a",
              objective: "Create homepage",
              output: {
                file_proposals: [
                  {
                    path: "src/app/page.tsx",
                    content: "export default function Page() {}",
                    step_index: 1,
                    node_id: "node-a",
                    workspace_root: "workspace/run-123",
                  },
                ],
              },
            },
            {
              alias: "child_2",
              node_id: "node-b",
              objective: "Add docs",
              output: {
                file_proposals: [
                  {
                    path: "docs/README.md",
                    content: "# Hello",
                    step_index: 2,
                    node_id: "node-b",
                    workspace_root: "workspace/run-123",
                  },
                ],
              },
            },
          ],
        })}
      />,
    );

    expect(screen.getByText("2 files")).toBeTruthy();
    expect(screen.getByText("client-app")).toBeTruthy();
    expect(screen.getAllByText("src/app/page.tsx").length).toBeGreaterThan(0);
    expect(screen.getByText("docs/README.md")).toBeTruthy();
    expect(screen.getByText("From: Create homepage")).toBeTruthy();
    expect(screen.getByText("From: Add docs")).toBeTruthy();
  });

  it("loads existing file content from the selected folder handle for review", async () => {
    const appDirectory: DirectoryHandleLike = {
      name: "app",
      getFileHandle: async (name: string) => {
        if (name !== "page.tsx") {
          throw new Error("not found");
        }
        return {
          getFile: async () => ({
            text: async () => "export default function ExistingPage() {}",
          }),
        };
      },
    };

    const srcDirectory: DirectoryHandleLike = {
      name: "src",
      getDirectoryHandle: async (name: string) => {
        if (name !== "app") {
          throw new Error("not found");
        }
        return appDirectory;
      },
    };

    const rootDirectory: DirectoryHandleLike = {
      name: "client-app",
      getDirectoryHandle: async (name: string) => {
        if (name !== "src") {
          throw new Error("not found");
        }
        return srcDirectory;
      },
      getFileHandle: async () => {
        throw new Error("not found");
      },
    };

    render(
      <ProposedFilesPanel
        selectedFolderName="client-app"
        selectedFolderHandle={rootDirectory}
        result={makeResult({
          file_proposals: [
            {
              path: "src/app/page.tsx",
              content: "export default function ProposedPage() {}",
              step_index: 1,
              node_id: "node-a",
            },
          ],
        })}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("existing file")).toBeTruthy();
      expect(screen.getByText("export default function ExistingPage() {}")).toBeTruthy();
    });

    expect(screen.getAllByText("export default function ProposedPage() {}").length).toBeGreaterThan(0);
  });

  it("applies the selected proposal into the target folder and refreshes current content", async () => {
    const files = new Map<string, string>([["src/app/page.tsx", "export default function ExistingPage() {}"]]);

    const makeDirectory = (prefix = ""): DirectoryHandleLike => ({
      name: prefix.split("/").filter(Boolean).slice(-1)[0] ?? "root",
      getDirectoryHandle: async (name: string, options?: { create?: boolean }) => {
        const nextPrefix = prefix ? `${prefix}/${name}` : name;
        const hasPrefix = Array.from(files.keys()).some((key) => key.startsWith(`${nextPrefix}/`));
        const exactFile = files.has(nextPrefix);
        if (!hasPrefix && !exactFile && !options?.create) {
          throw new Error("not found");
        }
        return makeDirectory(nextPrefix);
      },
      getFileHandle: async (name: string, options?: { create?: boolean }) => {
        const path = prefix ? `${prefix}/${name}` : name;
        if (!files.has(path) && !options?.create) {
          throw new Error("not found");
        }
        if (!files.has(path) && options?.create) {
          files.set(path, "");
        }
        return {
          getFile: async () => ({
            text: async () => files.get(path) ?? "",
          }),
          createWritable: async () => ({
            write: async (content: string) => {
              files.set(path, content);
            },
            close: async () => {},
          }),
        };
      },
    });

    render(
      <ProposedFilesPanel
        selectedFolderName="client-app"
        selectedFolderHandle={makeDirectory()}
        result={makeResult({
          file_proposals: [
            {
              path: "src/app/page.tsx",
              content: "export default function ProposedPage() {}",
              step_index: 1,
              node_id: "node-a",
            },
          ],
        })}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("existing file")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Apply selected" }));

    await waitFor(() => {
      expect(screen.getByText("applied")).toBeTruthy();
      expect(
        screen.getAllByText(/Applied src\/app\/page\.tsx to the selected target folder\./).length,
      ).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText("export default function ProposedPage() {}").length).toBeGreaterThan(1);
    });

    expect(files.get("src/app/page.tsx")).toBe("export default function ProposedPage() {}");
  });
});
