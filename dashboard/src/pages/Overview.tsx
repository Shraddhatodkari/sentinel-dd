import { Fragment } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmt } from "../api";
import { useAuth } from "../auth";
import { BandBadge, Bar, Card, Empty, ErrorBox, Kpi, Loading, OutcomeBadge, StatusBadge, useLoad } from "../components/ui";
import type { CaseSummary } from "../types";

interface Overview {
  total_cases: number;
  by_status: Record<string, number>;
  by_band: Record<string, number>;
  by_policy_outcome: Record<string, number>;
  pending_reviews: number;
  recent: CaseSummary[];
  sources: { source_id: string; name: string; kind: string; stale: boolean; age_hours: number | null; current_records: number; enabled: boolean; freshness_sla_h: number | null; last_status: string | null }[];
}

interface SystemStatus {
  version: string;
  database: boolean;
  schema_revision: string | null;
  screening_records: Record<string, number>;
  circuit_breakers: Record<string, string>;
  llm: { provider: string; model: string };
  llm_usage: { provider: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number }[];
  peer_model: string | null;
  runner_mode: string;
  audit_records: number;
}

const BAND_COLORS: Record<string, string> = { HIGH: "var(--high)", MEDIUM: "var(--medium)", LOW: "var(--low)" };

export function OverviewPage() {
  const nav = useNavigate();
  const { can } = useAuth();
  const { data, error } = useLoad(() => api<Overview>("/api/v1/overview"), [], (d) => (d && (d.by_status.running || d.by_status.queued) ? 5000 : 30000));
  const sys = useLoad(() => (can("system:read") ? api<SystemStatus>("/api/v1/system/status") : Promise.resolve(null)), []);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const assessed = Object.values(data.by_band).reduce((a, b) => a + b, 0);
  const inFlight = (data.by_status.running ?? 0) + (data.by_status.queued ?? 0);
  const decided = (data.by_status.approved ?? 0) + (data.by_status.rejected ?? 0) + (data.by_status.cleared ?? 0);
  const enabled = data.sources.filter((s) => s.enabled && s.freshness_sla_h);
  const stale = enabled.filter((s) => s.stale);
  const failed = enabled.filter((s) => s.last_status === "failed");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Executive risk overview</h1>
          <p>All figures are computed live from the case database for your tenant. Nothing on this page is sample data.</p>
        </div>
        {can("case:create") && (
          <Link className="btn btn-primary" to="/investigations/new">New investigation</Link>
        )}
      </div>
      <div className="grid g4">
        <Kpi label="Investigations" value={data.total_cases} sub={`${inFlight} in progress · ${decided} closed`} />
        <Kpi label="High risk" value={data.by_band.HIGH ?? 0} tone="high" sub={`of ${assessed} assessed`} />
        <Kpi label="Pending human review" value={data.pending_reviews} tone={data.pending_reviews ? "medium" : undefined} sub="maker-checker queue" />
        <Kpi
          label="Reference lists"
          value={`${enabled.length - stale.length}/${enabled.length}`}
          tone={stale.length || failed.length ? "medium" : "low"}
          sub={stale.length ? `${stale.length} stale · ${failed.length} failed` : failed.length ? `${failed.length} last run failed` : "within freshness SLA"}
        />
      </div>

      <div className="grid g-main section-gap">
        <Card title="Recent investigations" actions={<Link to="/cases">View all</Link>} pad={false}>
          {data.recent.length === 0 ? (
            <Empty>No investigations yet. Start one from “New investigation”.</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Entity</th><th>Status</th><th>Band</th><th className="num">Score</th><th>Policy</th><th>Opened</th></tr>
                </thead>
                <tbody>
                  {data.recent.map((c) => (
                    <tr key={c.case_id} className="clickable" onClick={() => nav(`/cases/${c.case_id}`)}>
                      <td><strong>{c.resolved_name ?? c.entity_query}</strong><div className="muted small">{c.case_id}</div></td>
                      <td><StatusBadge status={c.status} /></td>
                      <td><BandBadge band={c.risk_band} /></td>
                      <td className="num">{c.risk_score ?? "—"}</td>
                      <td><OutcomeBadge outcome={c.policy_outcome} /></td>
                      <td className="nowrap">{fmt.ago(c.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <div className="grid">
          <Card title="Risk band distribution">
            {assessed === 0 ? (
              <Empty>No assessed cases.</Empty>
            ) : (
              <>
                <div className="stack" style={{ marginBottom: 12 }}>
                  {(["HIGH", "MEDIUM", "LOW"] as const).map((b) => (
                    <span key={b} style={{ width: `${((data.by_band[b] ?? 0) / assessed) * 100}%`, background: BAND_COLORS[b] }} />
                  ))}
                </div>
                {(["HIGH", "MEDIUM", "LOW"] as const).map((b) => (
                  <div key={b} style={{ display: "grid", gridTemplateColumns: "80px 1fr 40px", gap: 10, alignItems: "center", marginBottom: 6 }}>
                    <BandBadge band={b} />
                    <Bar value={data.by_band[b] ?? 0} max={assessed} color={BAND_COLORS[b]} />
                    <span className="num" style={{ textAlign: "right" }}>{data.by_band[b] ?? 0}</span>
                  </div>
                ))}
              </>
            )}
          </Card>
          <Card title="Policy outcomes">
            {Object.keys(data.by_policy_outcome).length === 0 ? (
              <Empty>No policy decisions yet.</Empty>
            ) : (
              <dl className="kv">
                {Object.entries(data.by_policy_outcome).map(([k, v]) => (
                  <Fragment key={k}><dt><OutcomeBadge outcome={k} /></dt><dd className="num">{v}</dd></Fragment>
                ))}
              </dl>
            )}
          </Card>
        </div>
      </div>

      <div className="grid g2 section-gap">
        <Card title="Source freshness" actions={<Link to="/sources">Lineage &amp; runs</Link>} pad={false}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Source</th><th>Kind</th><th className="num">Records</th><th>Age</th><th>State</th></tr></thead>
              <tbody>
                {data.sources.map((s) => (
                  <tr key={s.source_id}>
                    <td>{s.name}</td>
                    <td className="muted">{s.kind}</td>
                    <td className="num">{s.current_records || "—"}</td>
                    <td className="nowrap">{s.age_hours == null ? "never" : `${s.age_hours.toFixed(1)} h`}</td>
                    <td>
                      {!s.enabled ? <span className="badge b-info">disabled</span>
                        : s.stale ? <span className="badge b-high">stale</span>
                        : s.last_status === "failed" ? <span className="badge b-medium">last run failed</span>
                        : !s.freshness_sla_h && s.age_hours == null ? <span className="badge b-medium" title="No successful retrieval yet; cases record this source as unavailable">not yet retrieved</span>
                        : !s.freshness_sla_h ? <span className="badge b-info">queried per case</span>
                        : <span className="badge b-low">fresh</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title="System health">
          {sys.error ? <ErrorBox error={sys.error} /> : !sys.data ? (
            can("system:read") ? <Loading /> : <Empty>System status requires the system:read permission.</Empty>
          ) : (
            <dl className="kv">
              <dt>Version</dt><dd>{sys.data.version}</dd>
              <dt>Database</dt><dd>{sys.data.database ? <span className="badge b-low">reachable</span> : <span className="badge b-high">unreachable</span>} <span className="muted small">schema {sys.data.schema_revision ?? "unknown"}</span></dd>
              <dt>Runner</dt><dd>{sys.data.runner_mode}</dd>
              <dt>LLM provider</dt><dd>{sys.data.llm.provider} · {sys.data.llm.model}</dd>
              <dt>Peer model</dt><dd>{sys.data.peer_model ?? <span className="muted">not trained</span>}</dd>
              <dt>Screening records</dt><dd className="num">{Object.values(sys.data.screening_records).reduce((a, b) => a + b, 0).toLocaleString()}</dd>
              <dt>Circuit breakers</dt>
              <dd>
                {Object.keys(sys.data.circuit_breakers).length === 0 ? <span className="muted">no external calls yet</span>
                  : Object.entries(sys.data.circuit_breakers).map(([h, st]) => (
                    <span key={h} className={`badge ${st === "closed" ? "b-low" : st === "open" ? "b-high" : "b-medium"}`} style={{ marginRight: 4, marginBottom: 4 }}>{h}: {st}</span>
                  ))}
              </dd>
              <dt>LLM usage</dt>
              <dd>
                {sys.data.llm_usage.length === 0 ? <span className="muted">none recorded</span> : sys.data.llm_usage.map((u) => (
                  <div key={u.provider} className="small">{u.provider}: {u.calls} calls · {(u.input_tokens + u.output_tokens).toLocaleString()} tokens · ${u.cost_usd.toFixed(4)}</div>
                ))}
              </dd>
              <dt>Audit records</dt><dd className="num">{sys.data.audit_records.toLocaleString()}</dd>
            </dl>
          )}
        </Card>
      </div>
    </>
  );
}
