const RAW_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export const DEFAULT_BASE_URL = RAW_BASE_URL.trim().replace(/\/+$/, "");
