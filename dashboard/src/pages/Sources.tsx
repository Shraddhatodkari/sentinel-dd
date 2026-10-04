import { useState } from "react";
import { api, fmt } from "../api";
import { useAuth } from "../auth";
import { Card, Empty, ErrorBox, Loading, useLoad } from "../components/ui";
import type { SourceStatus } from "../types";

interface Run {
  id: string;
  source_id: string;
  kind: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  records_loaded: number;
  records_rejected: number;
  unchanged: boolean;
  quality: Record<string, unknown> | null;
  error: string | null;
  document_id: string | null;
}

export function SourcesPage() {
  const { can } = useAuth();
  const canRefresh = can("source:refresh");
  const canRuns = canRefresh || can("system:read");
  const src = useLoad(() => api<SourceStatus[]>("/api/v1/sources"), []);
  const runs = useLoad(() => (canRuns ? api<Run[]>("/api/v1/sources/runs?limit=60") : Promise.resolve(null)), [], (d) => (d?.some((r) => r.status === "running") ? 4000 : null));
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>();
  const refresh = async (id: string) => {
    setErr(undefined);
    try {
      await api(`/api/v1/sources/${id}/refresh`, { method: "POST" });
      setMsg(`Refresh of ${id} started.`);
      window.setTimeout(() => { src.reload(); runs.reload(); }, 1500);
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Data sources &amp; lineage</h1>
          <p>Authoritative public sources only. Each retrieval is stored content-addressed (SHA-256) with URL and timestamp, so every evidence item traces back to raw bytes.</p>
        </div>
      </div>
      {msg && <div className="alert alert-ok" style={{ marginBottom: 12 }}>{msg}</div>}
      <ErrorBox error={err || src.error} />
      <Card title="Source registry" pad={false}>
        {!src.data ? <Loading /> : (
          <div className="table-wrap">
            <table data-testid="sources-table">
              <thead><tr><th>Source</th><th>Authority / licence</th><th>Kind</th><th className="num">Records</th><th>Last retrieved</th><th>SLA</th><th>State</th><th>Last run</th>{canRefresh && <th />}</tr></thead>
              <tbody>
                {src.data.map((s) => (
                  <tr key={s.source_id}>
                    <td><strong>{s.name}</strong><div className="small"><a href={s.url} target="_blank" rel="noreferrer noopener">{s.source_id}</a></div></td>
                    <td className="small">{s.authority}<div className="muted">{s.license}</div></td>
                    <td>{s.kind}</td>
                    <td className="num">{s.current_records || "—"}</td>
                    <td className="nowrap small">{fmt.date(s.last_retrieved_at)}<div className="muted">{s.age_hours == null ? "" : `${s.age_hours.toFixed(1)} h ago`}</div></td>
                    <td className="small">{s.freshness_sla_h ? `${s.freshness_sla_h} h` : "on demand"}</td>
                    <td>{!s.enabled ? <span className="badge b-info">disabled</span> : s.stale ? <span className="badge b-high">stale</span> : !s.freshness_sla_h ? <span className="badge b-info">queried per case</span> : <span className="badge b-low">fresh</span>}</td>
                    <td className="small">{s.last_run ? <><span className={`badge ${s.last_run.status === "succeeded" ? "b-low" : s.last_run.status === "failed" ? "b-high" : "b-info"}`}>{s.last_run.status}</span>{s.last_run.unchanged && <span className="muted"> unchanged</span>}{s.last_run.error && <div style={{ color: "var(--high)" }}>{s.last_run.error.slice(0, 120)}</div>}</> : "—"}</td>
                    {canRefresh && <td>{s.enabled && (s.kind === "sanctions" || s.kind === "pep") && <button className="btn" onClick={() => refresh(s.source_id)}>Refresh</button>}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="section-gap" />
      <Card title="Ingestion runs" pad={false}>
        {!canRuns ? <Empty>Viewing ingestion runs requires system:read or source:refresh.</Empty> : runs.error ? <ErrorBox error={runs.error} /> : !runs.data ? <Loading /> : runs.data.length === 0 ? <Empty>No ingestion runs yet.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Started</th><th>Source</th><th>Status</th><th className="num">Loaded</th><th className="num">Rejected</th><th>Data-quality checks</th><th>Document</th></tr></thead>
              <tbody>
                {runs.data.map((r) => (
                  <tr key={r.id}>
                    <td className="nowrap small">{fmt.date(r.started_at)}</td>
                    <td>{r.source_id}</td>
                    <td><span className={`badge ${r.status === "succeeded" ? "b-low" : r.status === "failed" ? "b-high" : "b-info"}`}>{r.status}</span>{r.unchanged && <div className="small muted">content unchanged</div>}{r.error && <div className="small" style={{ color: "var(--high)" }}>{r.error.slice(0, 160)}</div>}</td>
                    <td className="num">{r.records_loaded}</td>
                    <td className="num">{r.records_rejected}</td>
                    <td className="small wrap" style={{ maxWidth: 380 }}>{r.quality ? JSON.stringify(r.quality).slice(0, 260) : "—"}</td>
                    <td className="small">{r.document_id ? <span className="cite">{r.document_id.slice(0, 12)}</span> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
