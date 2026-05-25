import { describe, it, expect } from "vitest";
import { snippetFromUnknown, isTerminalStatus } from "@/lib/formatUtils";
import { normalizeNodeStatus } from "@/lib/statusUtils";

describe("snippetFromUnknown", () => {
  it("returns default for null/undefined", () => {
    expect(snippetFromUnknown(null)).toBe("No data available.");
    expect(snippetFromUnknown(undefined)).toBe("No data available.");
  });

  it("returns custom default", () => {
    expect(snippetFromUnknown(null, "custom")).toBe("custom");
  });

  it("returns string as-is", () => {
    expect(snippetFromUnknown("hello")).toBe("hello");
  });

  it("stringifies objects", () => {
    const result = snippetFromUnknown({ a: 1 });
    expect(result).toContain('"a": 1');
  });

  it("handles arrays", () => {
    const result = snippetFromUnknown([1, 2]);
    expect(result).toBe("[\n  1,\n  2\n]");
  });

  it("falls back to String for non-serializable", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    const result = snippetFromUnknown(circular);
    expect(typeof result).toBe("string");
  });
});

describe("isTerminalStatus", () => {
  it("returns true for terminal statuses", () => {
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
    expect(isTerminalStatus("canceled")).toBe(true);
    expect(isTerminalStatus("cancelled")).toBe(true);
  });

  it("returns false for non-terminal statuses", () => {
    expect(isTerminalStatus("running")).toBe(false);
    expect(isTerminalStatus("queued")).toBe(false);
    expect(isTerminalStatus("blocked_human")).toBe(false);
    expect(isTerminalStatus("merged")).toBe(false);
    expect(isTerminalStatus("unknown")).toBe(false);
  });
});

describe("normalizeNodeStatus", () => {
  it("returns direct matches", () => {
    expect(normalizeNodeStatus("queued")).toBe("queued");
    expect(normalizeNodeStatus("running")).toBe("running");
    expect(normalizeNodeStatus("blocked_human")).toBe("blocked_human");
    expect(normalizeNodeStatus("completed")).toBe("completed");
    expect(normalizeNodeStatus("failed")).toBe("failed");
    expect(normalizeNodeStatus("merged")).toBe("merged");
  });

  it("maps backend variants", () => {
    expect(normalizeNodeStatus("waiting_check")).toBe("running");
    expect(normalizeNodeStatus("failed_check")).toBe("failed");
    expect(normalizeNodeStatus("error")).toBe("failed");
  });

  it("returns fallback for unknown", () => {
    expect(normalizeNodeStatus("unknown_status")).toBe("queued");
    expect(normalizeNodeStatus("unknown_status", "running")).toBe("running");
  });

  it("returns fallback for undefined", () => {
    expect(normalizeNodeStatus(undefined)).toBe("queued");
    expect(normalizeNodeStatus(undefined, "completed")).toBe("completed");
  });
});
