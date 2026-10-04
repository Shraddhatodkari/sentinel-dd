import type { Session } from "./types";

const KEY = "sentinel.session";

export function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    return s.expires_at * 1000 > Date.now() ? s : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null): void {
  try {
    if (s) sessionStorage.setItem(KEY, JSON.stringify(s));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: session lives in memory only */
  }
}

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(typeof body === "object" && body && "detail" in body ? describe((body as { detail: unknown }).detail) : `HTTP ${status}`);
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? JSON.stringify(d)).join("; ");
  if (detail && typeof detail === "object") {
    const d = detail as { error?: string; violations?: string[] };
    if (d.violations) return `${d.error}: ${d.violations.join(", ")}`;
    return JSON.stringify(detail);
  }
  return "request failed";
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const session = loadSession();
  const headers = new Headers(init.headers);
  if (session) headers.set("Authorization", `Bearer ${session.access_token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, { ...init, headers, body });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && session) {
    saveSession(null);
    onUnauthorized();
  }
  if (!res.ok) throw new ApiError(res.status, data);
  return data as T;
}

export const fmt = {
  date: (s: string | null | undefined) => {
    if (!s) return "—";
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? s : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  },
  ago: (s: string | null | undefined) => {
    if (!s) return "—";
    const m = Math.round((Date.now() - new Date(s).getTime()) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return `${m} min ago`;
    const h = Math.round(m / 60);
    return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
  },
  money: (v: unknown) => {
    if (typeof v !== "number") return "—";
    const a = Math.abs(v);
    const s = a >= 1e9 ? `${(a / 1e9).toFixed(2)}B` : a >= 1e6 ? `${(a / 1e6).toFixed(2)}M` : a.toLocaleString();
    return `${v < 0 ? "−" : ""}$${s}`;
  },
  pct: (v: unknown, digits = 1) => (typeof v === "number" ? `${(v * 100).toFixed(digits)}%` : "—"),
  num: (v: unknown, digits = 2) => (typeof v === "number" ? v.toFixed(digits) : "—"),
  label: (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
};
