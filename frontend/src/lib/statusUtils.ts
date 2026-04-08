import type { NodeStatus } from "@/types/contracts";

/**
 * Normalize backend node status values to canonical frontend status.
 * Handles backend variants like "waiting_check", "failed_check", "error".
 */
export function normalizeNodeStatus(value: string | undefined, fallback: NodeStatus = "queued"): NodeStatus {
  if (!value) return fallback;

  // Direct matches
  if (
    value === "queued" ||
    value === "running" ||
    value === "blocked_human" ||
    value === "completed" ||
    value === "failed" ||
    value === "merged"
  ) {
    return value;
  }

  // Backend variants → canonical status
  if (value === "waiting_check") return "running";
  if (value === "failed_check" || value === "error") return "failed";

  return fallback;
}
