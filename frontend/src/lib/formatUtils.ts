/** Format an unknown value as a human-readable snippet. */
export function snippetFromUnknown(
  value: unknown,
  defaultValue = "No data available.",
): string {
  if (value == null) return defaultValue;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** Check if a run status is terminal (completed/failed/canceled). */
export function isTerminalStatus(status: string): boolean {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "canceled" ||
    status === "cancelled"
  );
}
