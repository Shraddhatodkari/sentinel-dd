import { useEffect, useState, type ReactNode } from "react";
import { ApiError } from "../api";
import type { Band } from "../types";

export function BandBadge({ band }: { band: Band | null | undefined }) {
  if (!band) return <span className="badge b-info">Not assessed</span>;
  const cls = band === "HIGH" ? "b-high" : band === "MEDIUM" ? "b-medium" : "b-low";
  return <span className={`badge ${cls}`}>{band}</span>;
}

const STATUS: Record<string, [string, string]> = {
  queued: ["b-info", "Queued"],
  running: ["b-accent", "Running"],
  awaiting_review: ["b-medium", "Awaiting review"],
  approved: ["b-low", "Approved"],
  rejected: ["b-high", "Rejected"],
  cleared: ["b-low", "Auto-cleared"],
  failed: ["b-high", "Failed"],
};

export function StatusBadge({ status }: { status: string }) {
  const [cls, label] = STATUS[status] ?? ["b-info", status];
  return <span className={`badge ${cls}`}>{label}</span>;
}

export function OutcomeBadge({ outcome }: { outcome: string | null | undefined }) {
  if (!outcome) return <span className="muted">—</span>;
  const cls = outcome === "ESCALATE" ? "b-high" : outcome === "REVIEW" ? "b-medium" : "b-low";
  return <span className={`badge ${cls}`}>{outcome.replace("_", " ")}</span>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  const cls = severity === "critical" || severity === "high" ? "b-high" : severity === "medium" ? "b-medium" : severity === "low" ? "b-info" : "b-info";
  return <span className={`badge ${cls}`}>{severity}</span>;
}

export function CoverageBadge({ status }: { status: string | undefined }) {
  const map: Record<string, string> = { ok: "b-low", partial: "b-medium", unavailable: "b-high", not_applicable: "b-info" };
  return <span className={`badge ${map[status ?? ""] ?? "b-info"}`}>{(status ?? "n/a").replace("_", " ")}</span>;
}

export function Card({ title, actions, children, pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; pad?: boolean }) {
  return (
    <section className="card">
      {title !== undefined && (
        <div className="card-head">
          <h2>{title}</h2>
          {actions}
        </div>
      )}
      {pad ? <div className="card-body">{children}</div> : children}
    </section>
  );
}

export function Kpi({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "high" | "medium" | "low" }) {
  const color = tone ? `var(--${tone})` : undefined;
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value" style={{ color }}>
        {value}
      </div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof ApiError ? `${error.message} (HTTP ${error.status})` : error instanceof Error ? error.message : String(error);
  return <div className="alert alert-error" role="alert">{msg}</div>;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="empty">
      <span className="spinner" /> <span style={{ marginLeft: 8 }}>{label}…</span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/** Fetch helper hook with polling support. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[], pollMs?: (data: T | undefined) => number | null) {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<unknown>();
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    fn()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setError(undefined);
        const next = pollMs?.(d);
        if (next) timer = window.setTimeout(() => setTick((t) => t + 1), next);
      })
      .catch((e) => !cancelled && setError(e));
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}

export function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="bar" aria-label={`${value} of ${max}`}>
      <span style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}
