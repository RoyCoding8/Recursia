"use client";

export interface FileLike {
  text: () => Promise<string>;
}

export interface WritableLike {
  write: (content: string) => Promise<void>;
  close: () => Promise<void>;
}

export interface FileHandleLike {
  getFile: () => Promise<FileLike>;
  createWritable?: () => Promise<WritableLike>;
}

export interface DirectoryHandleLike {
  name: string;
  getDirectoryHandle?: (
    name: string,
    options?: { create?: boolean },
  ) => Promise<DirectoryHandleLike>;
  getFileHandle?: (
    name: string,
    options?: { create?: boolean },
  ) => Promise<FileHandleLike>;
}

export interface ExistingFileReview {
  status: "unsupported" | "no_target" | "missing" | "existing" | "error";
  content?: string;
  message: string;
}

export interface ApplyResult {
  status: "success" | "unsupported" | "no_target" | "error";
  message: string;
}

export async function reviewExistingFile(
  root: DirectoryHandleLike | undefined,
  relativePath: string,
): Promise<ExistingFileReview> {
  if (!root) {
    return {
      status: "no_target",
      message: "Choose a target folder to compare staged content against existing files.",
    };
  }

  if (typeof root.getDirectoryHandle !== "function" || typeof root.getFileHandle !== "function") {
    return {
      status: "unsupported",
      message: "Directory review is not supported by the current folder handle.",
    };
  }

  const segments = relativePath.split("/").filter(Boolean);
  if (segments.length === 0) {
    return {
      status: "error",
      message: "Invalid proposal path.",
    };
  }

  try {
    let directory = root;
    for (const segment of segments.slice(0, -1)) {
      if (typeof directory.getDirectoryHandle !== "function") {
        return {
          status: "unsupported",
          message: "Directory traversal is not supported by the current folder handle.",
        };
      }
      directory = await directory.getDirectoryHandle(segment);
    }

    if (typeof directory.getFileHandle !== "function") {
      return {
        status: "unsupported",
        message: "File access is not supported by the current folder handle.",
      };
    }

    const fileHandle = await directory.getFileHandle(segments[segments.length - 1]);
    const file = await fileHandle.getFile();
    const content = await file.text();

    return {
      status: "existing",
      content,
      message: "Existing file content loaded from the selected target folder.",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.toLowerCase().includes("not found") || message.toLowerCase().includes("unable to locate")) {
      return {
        status: "missing",
        message: "This proposal creates a new file in the selected target folder.",
      };
    }

    return {
      status: "error",
      message: `Failed to load current file content: ${message}`,
    };
  }
}

export async function applyFileToDirectory(
  root: DirectoryHandleLike | undefined,
  relativePath: string,
  content: string,
): Promise<ApplyResult> {
  if (!root) {
    return {
      status: "no_target",
      message: "Choose a target folder before applying file changes.",
    };
  }

  if (typeof root.getDirectoryHandle !== "function" || typeof root.getFileHandle !== "function") {
    return {
      status: "unsupported",
      message: "This folder handle does not support writing files.",
    };
  }

  const segments = relativePath.split("/").filter(Boolean);
  if (segments.length === 0) {
    return {
      status: "error",
      message: "Invalid proposal path.",
    };
  }

  try {
    let directory = root;
    for (const segment of segments.slice(0, -1)) {
      if (typeof directory.getDirectoryHandle !== "function") {
        return {
          status: "unsupported",
          message: "Directory creation is not supported by the current folder handle.",
        };
      }
      directory = await directory.getDirectoryHandle(segment, { create: true });
    }

    if (typeof directory.getFileHandle !== "function") {
      return {
        status: "unsupported",
        message: "File creation is not supported by the current folder handle.",
      };
    }

    const fileHandle = await directory.getFileHandle(segments[segments.length - 1], { create: true });
    if (typeof fileHandle.createWritable !== "function") {
      return {
        status: "unsupported",
        message: "Writable file handles are not supported in this environment.",
      };
    }

    const writable = await fileHandle.createWritable();
    await writable.write(content);
    await writable.close();

    return {
      status: "success",
      message: `Applied ${relativePath} to the selected target folder.`,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      status: "error",
      message: `Failed to apply ${relativePath}: ${message}`,
    };
  }
}
