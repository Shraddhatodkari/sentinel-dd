import { Fragment, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmt } from "../api";
import { useAuth } from "../auth";
import { RelationshipGraph } from "../components/Graph";
import { Markdown } from "../components/Markdown";
import { BandBadge, Bar, Card, CoverageBadge, Empty, ErrorBox, Loading, OutcomeBadge, SeverityBadge, StatusBadge, useLoad } from "../components/ui";
import type { AuditRecord, CaseDetail, EvidenceItem, SignalItem } from "../types";
import { ReviewPanel } from "./ReviewPanel";

const TABS = ["overview", "evidence", "risk", "screening", "ownership", "media", "financials", "report", "trace", "audit"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABEL: Record<Tab, string> = {
  overview: "Overview",
  evidence: "Evidence",
  risk: "Risk scorecard",
  screening: "Sanctions & PEP",
  ownership: "Ownership & UBO",
  media: "Adverse media",
  financials: "Financials",
  report: "Report",
  trace: "Agent trace",
  audit: "Case audit",
};

export function CaseDetailPage() {
  const { caseId = "" } = useParams();
  const [tab, setTab] = useState<Tab>("overview");
  const { data, error, reload } = useLoad(
    () => api<CaseDetail>(`/api/v1/cases/${encodeURIComponent(caseId)}`),
    [caseId],
    (d) => (d && (d.status === "queued" || d.status === "running") ? 2500 : null),
  );
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading label="Loading case" />;
  const inFlight = data.status === "queued" || data.status === "running";
  const counts: Partial<Record<Tab, number>> = {
    evidence: data.evidence.length,
    screening: data.signals.filter((s) => s.signal_type.startsWith("sanctions") || s.signal_type.startsWith("pep")).length,
    media: data.signals.filter((s) => s.signal_type.startsWith("adverse_media")).length,
  };
  return (
    <>
      <div className="page-head">
        <div>
          <div className="muted small">
            <Link to="/cases">Case explorer</Link> / {data.case_id}
          </div>
          <h1 data-testid="case-title">{data.resolved_name ?? data.entity_query}</h1>
          {data.resolved_name && data.resolved_name !== data.entity_query && <div className="muted small" data-testid="case-query">requested as “{data.entity_query}”</div>}
          <p>
            <StatusBadge status={data.status} /> <BandBadge band={data.risk_band} /> <OutcomeBadge outcome={data.policy_outcome} />{" "}
            <span className="muted small">
              opened by {data.created_by} · {fmt.date(data.created_at)}
              {data.attempts > 1 && ` · attempt ${data.attempts}`}
            </span>
          </p>
        </div>
        {data.risk_score != null && (
          <div className="card kpi" style={{ minWidth: 170 }}>
            <div className="label">Risk score</div>
            <div className="value" data-testid="risk-score">{data.risk_score}<span className="muted small"> / 100</span></div>
            <div className="sub">confidence {fmt.pct(data.assessment?.confidence, 0)}</div>
          </div>
        )}
      </div>

      {inFlight && <Progress data={data} />}
      {data.status === "failed" && <FailedBanner data={data} onRetry={reload} />}

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {TAB_LABEL[t]}
            {counts[t] !== undefined && <span className="n">{counts[t]}</span>}
          </button>
        ))}
      </div>

      {tab === "overview" && <OverviewTab data={data} onDecided={reload} />}
      {tab === "evidence" && <EvidenceTab evidence={data.evidence} />}
      {tab === "risk" && <RiskTab data={data} />}
      {tab === "screening" && <ScreeningTab data={data} />}
      {tab === "ownership" && <OwnershipTab data={data} />}
      {tab === "media" && <MediaTab data={data} />}
      {tab === "financials" && <FinancialsTab data={data} />}
      {tab === "report" && <ReportTab data={data} />}
      {tab === "trace" && <TraceTab data={data} />}
      {tab === "audit" && <CaseAuditTab caseId={data.case_id} />}
    </>
  );
}

const NODES = ["intake", "resolve_entity", "planner", "corporate_registry", "financials", "adverse_media", "filings", "screening", "research", "graph_analyst", "risk_assessor", "report_writer", "critic", "policy"];

function Progress({ data }: { data: CaseDetail }) {
  const done = new Set(data.events.filter((e) => e.status === "completed" || e.status === "degraded").map((e) => e.node));
  const last = data.events[data.events.length - 1];
  const pct = Math.round((NODES.filter((n) => done.has(n)).length / NODES.length) * 100);
  return (
    <div className="alert alert-info" style={{ marginBottom: 16 }} data-testid="progress">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
        <span><span className="spinner" /> Investigation {data.status} — {last ? `last step: ${fmt.label(last.node)}` : "waiting for worker"}</span>
        <span className="num">{Math.min(pct, 99)}%</span>
      </div>
      <Bar value={Math.min(pct, 99)} max={100} color="var(--accent)" />
    </div>
  );
}

function FailedBanner({ data, onRetry }: { data: CaseDetail; onRetry: () => void }) {
  const { can } = useAuth();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>();
  const retry = async () => {
    setBusy(true);
    try {
      await api(`/api/v1/cases/${data.case_id}/retry`, { method: "POST" });
      onRetry();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="alert alert-error" style={{ marginBottom: 16 }}>
      <strong>Investigation failed.</strong> {data.error}
      <div className="small" style={{ marginTop: 4 }}>Partial evidence collected before the failure is preserved. Retrying resumes from the last checkpoint.</div>
      {can("case:create") && (
        <button className="btn" style={{ marginTop: 8 }} onClick={retry} disabled={busy}>{busy ? "Retrying…" : "Retry investigation"}</button>
      )}
      <ErrorBox error={err} />
    </div>
  );
}

function evidenceMap(data: CaseDetail): Map<string, EvidenceItem> {
  return new Map(data.evidence.map((e) => [e.id, e]));
}

function EvidenceRefs({ ids, data }: { ids: string[]; data: CaseDetail }) {
  const map = evidenceMap(data);
  if (!ids.length) return <span className="muted small">derived (no direct evidence)</span>;
  return (
    <span>
      {ids.map((id) => {
        const ev = map.get(id);
        return (
          <span key={id} className="cite" title={ev ? `${ev.title} — ${ev.source_url}` : id} style={{ marginRight: 4 }}>
            {id.slice(0, 12)}
          </span>
        );
      })}
    </span>
  );
}

/* ------------------------------------------------------------------ overview */
function OverviewTab({ data, onDecided }: { data: CaseDetail; onDecided: () => void }) {
  const res = (data.summary.resolution ?? {}) as Record<string, unknown>;
  const facts = data.evidence.length;
  const sigs = data.signals.length;
  const sources = new Set(data.evidence.map((e) => e.source_id));
  return (
    <div className="grid">
      <Card title="Decision chain">
        <div className="chain" data-testid="decision-chain">
          <div className="step"><div className="k">1 · Source facts</div><div className="v">{facts} evidence items</div><div className="d">from {sources.size} authoritative sources, each with URL, retrieval time and hash</div></div>
          <div className="step"><div className="k">2 · Signals</div><div className="v">{sigs} extracted</div><div className="d">deterministic rules over evidence; each cites its evidence</div></div>
          <div className="step"><div className="k">3 · Assessment</div><div className="v">{data.assessment ? <><BandBadge band={data.assessment.band} /> {data.assessment.score}</> : "—"}</div><div className="d">{data.assessment?.scorecard_version ?? "not yet assessed"}</div></div>
          <div className="step"><div className="k">4 · Policy</div><div className="v"><OutcomeBadge outcome={data.policy?.outcome} /></div><div className="d">{data.policy ? `${data.policy.policy_version} · ${data.policy.rules.length} rule(s) fired` : "pending"}</div></div>
          <div className="step"><div className="k">5 · Human decision</div><div className="v">{data.review?.status === "pending" ? <span className="badge b-medium">pending</span> : data.review ? <span className={`badge ${data.review.status === "approved" ? "b-low" : "b-high"}`}>{data.review.status}</span> : data.status === "cleared" ? <span className="badge b-low">auto-cleared</span> : "—"}</div><div className="d">{data.review?.decided_by ? `${data.review.decided_by} (${data.review.decided_role}) · ${fmt.date(data.review.decided_at)}` : data.review ? `requires ${data.review.required_role}` : ""}</div></div>
        </div>
        <div className="disclaimer">
          Sentinel distinguishes source facts, extracted signals, analytical assessment, policy decision and human decision. Screening results are
          <strong> potential matches</strong> requiring human confirmation; nothing on this page is a regulatory determination.
        </div>
      </Card>
      <div className="grid g-main">
        <div className="grid">
          {data.policy && (
            <Card title="Policy rules fired">
              {data.policy.rules.length === 0 ? <Empty>No rules fired.</Empty> : (
                <table>
                  <thead><tr><th>Rule</th><th>Reason</th><th>Outcome</th><th>Evidence</th></tr></thead>
                  <tbody>
                    {data.policy.rules.map((r) => (
                      <tr key={r.rule_id}><td className="nowrap"><strong>{r.rule_id}</strong></td><td>{r.description}</td><td><OutcomeBadge outcome={r.outcome} /></td><td><EvidenceRefs ids={r.evidence_ids} data={data} /></td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          )}
          <Card title="Entity resolution">
            <dl className="kv">
              <dt>Query</dt><dd>{data.entity_query}</dd>
              <dt>Resolved name</dt><dd>{String(res.name ?? "—")}</dd>
              <dt>SEC CIK</dt><dd>{res.cik ? <a href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${res.cik}`} target="_blank" rel="noreferrer noopener">{String(res.cik)}</a> : "—"}</dd>
              <dt>LEI</dt><dd>{res.lei ? <a href={`https://search.gleif.org/#/record/${res.lei}`} target="_blank" rel="noreferrer noopener">{String(res.lei)}</a> : "—"}</dd>
              <dt>Tickers</dt><dd>{Array.isArray(res.tickers) && res.tickers.length ? (res.tickers as string[]).join(", ") : "—"}</dd>
              <dt>Jurisdiction</dt><dd>{String(res.jurisdiction ?? "—")} {res.country ? `(${res.country})` : ""}</dd>
              <dt>Method / confidence</dt><dd>{String(res.method ?? "—")} · {fmt.pct(res.confidence, 0)} {res.ambiguous ? <span className="badge b-medium">ambiguous</span> : null}</dd>
            </dl>
            {Array.isArray(res.candidates) && (res.candidates as unknown[]).length > 1 && (
              <details style={{ marginTop: 10 }}>
                <summary className="small">Other candidates considered ({(res.candidates as unknown[]).length})</summary>
                <table className="section-gap">
                  <thead><tr><th>Source</th><th>Name</th><th>Identifier</th><th className="num">Score</th></tr></thead>
                  <tbody>
                    {(res.candidates as Record<string, unknown>[]).map((c, i) => (
                      <tr key={i}><td>{String(c.source)}</td><td>{String(c.name)}</td><td className="small">{String(c.cik ?? c.lei ?? "")}</td><td className="num">{fmt.num(c.score)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
          </Card>
        </div>
        <div className="grid">
          <ReviewPanel data={data} onDecided={onDecided} />
          <Card title="Coverage">
            {!data.summary.coverage ? <Empty>Not available yet.</Empty> : (
              <dl className="kv" data-testid="coverage">
                {Object.entries(data.summary.coverage).map(([k, v]) => (
                  <Fragment key={k}><dt>{fmt.label(k)}</dt><dd><CoverageBadge status={v} /></dd></Fragment>
                ))}
              </dl>
            )}
            {(data.summary.errors ?? []).length > 0 && (
              <div className="alert alert-warn section-gap small">
                <strong>Degraded collection</strong>
                <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{data.summary.errors!.map((e, i) => <li key={i}>{e}</li>)}</ul>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ evidence */
function EvidenceTab({ evidence }: { evidence: EvidenceItem[] }) {
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const kinds = useMemo(() => [...new Set(evidence.map((e) => e.kind))].sort(), [evidence]);
  const items = evidence.filter((e) => (!kind || e.kind === kind) && (!q || `${e.title} ${e.content}`.toLowerCase().includes(q.toLowerCase())));
  if (!evidence.length) return <Empty>No evidence collected yet.</Empty>;
  return (
    <>
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <div className="field"><label htmlFor="ek">Kind</label>
          <select id="ek" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All ({evidence.length})</option>{kinds.map((k) => <option key={k} value={k}>{fmt.label(k)} ({evidence.filter((e) => e.kind === k).length})</option>)}</select>
        </div>
        <div className="field"><label htmlFor="eq">Search</label><input id="eq" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      {items.map((e) => <EvidenceCard key={e.id} e={e} />)}
    </>
  );
}

function EvidenceCard({ e }: { e: EvidenceItem }) {
  const [doc, setDoc] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<unknown>();
  const verify = async () => {
    try {
      setDoc(await api(`/api/v1/documents/${e.document_id}`));
    } catch (x) {
      setErr(x);
    }
  };
  return (
    <details className="evidence" data-testid="evidence-item">
      <summary>
        <span className="badge b-info">{fmt.label(e.kind)}</span>
        <strong style={{ flex: 1 }}>{e.title}</strong>
        <span className="cite">{e.id.slice(0, 12)}</span>
      </summary>
      <div className="meta">
        <span>Source: <strong>{e.source_id}</strong></span>
        <span>Retrieved: {fmt.date(e.retrieved_at)}</span>
        <span>Locator: {e.locator || "—"}</span>
        <span>Confidence: {fmt.pct(e.confidence, 0)}</span>
        <a href={e.source_url} target="_blank" rel="noreferrer noopener">Open source ↗</a>
      </div>
      <div className="body">{e.content}{e.content_truncated && "\n…[truncated for display; full text is stored]"}</div>
      <div className="meta">
        <button className="btn" onClick={verify}>Lineage &amp; integrity</button>
        {doc && (
          <span>
            document {String(doc.id).slice(0, 12)} · sha256 {String(doc.sha256).slice(0, 16)}… · {String(doc.size_bytes)} bytes · HTTP {String(doc.http_status)} ·{" "}
            {doc.integrity_verified ? <span className="badge b-low">raw copy verified</span> : <span className="badge b-high">raw copy missing</span>}
          </span>
        )}
        <ErrorBox error={err} />
      </div>
    </details>
  );
}

/* ------------------------------------------------------------------ risk */
function RiskTab({ data }: { data: CaseDetail }) {
  const a = data.assessment;
  if (!a) return <Empty>The risk assessment has not run yet.</Empty>;
  return (
    <div className="grid">
      <Card title={<>Scorecard {a.scorecard_version} · <BandBadge band={a.band} /> {a.score} / 100</>}>
        <table data-testid="factors">
          <thead><tr><th>Factor</th><th>Observed value</th><th>Contribution</th><th className="num">Points / cap</th><th>Evidence</th></tr></thead>
          <tbody>
            {a.factors.map((f) => (
              <tr key={f.id}>
                <td style={{ maxWidth: 320 }}><strong>{f.name}</strong><div className="muted small" title={f.rules.join("; ")}>{f.id}{f.rules.length > 0 && ` · ${clip(f.rules.join("; "), 160)}`}</div></td>
                <td className="small">{f.data_available ? showValue(f.value) : <span className="badge b-medium">data unavailable</span>}</td>
                <td style={{ minWidth: 140 }}><Bar value={f.points} max={f.cap} color={f.points > 0 ? "var(--high)" : "var(--low)"} /></td>
                <td className="num">{f.points} / {f.cap}</td>
                <td><EvidenceRefs ids={f.evidence_ids} data={data} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="disclaimer">
          Score = Σ min(points, cap) over factors; bands LOW &lt; 25 ≤ MEDIUM &lt; 50 ≤ HIGH (see Governance). Confidence {fmt.pct(a.confidence, 0)} reflects source coverage, not
          probability of wrongdoing. Model versions: {Object.entries(a.model_versions).map(([k, v]) => `${k}=${v ?? "n/a"}`).join(" · ")}
        </div>
      </Card>
      <div className="grid g2">
        <Card title="Feature vector (reproducible inputs)">
          <dl className="kv">
            {Object.entries(a.features).map(([k, v]) => (
              <Fragment key={k}><dt>{k}</dt><dd className="small">{showValue(v)}</dd></Fragment>
            ))}
          </dl>
        </Card>
        <Card title={`Signals (${data.signals.length})`} pad={false}>
          <SignalTable signals={data.signals} data={data} />
        </Card>
      </div>
    </div>
  );
}

function clip(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function showValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") return String(+v.toFixed(3));
  if (typeof v === "object") return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${typeof x === "number" ? +x.toFixed(3) : String(x)}`).join(", ");
  return String(v);
}

function SignalTable({ signals, data }: { signals: SignalItem[]; data: CaseDetail }) {
  if (!signals.length) return <Empty>No signals.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Signal</th><th>Severity</th><th className="num">Conf.</th><th>Evidence</th></tr></thead>
        <tbody>
          {signals.map((s) => (
            <tr key={s.id}>
              <td><div className="small muted">{s.signal_type} · {s.derived_by}</div>{s.label}</td>
              <td><SeverityBadge severity={s.severity} /></td>
              <td className="num">{fmt.pct(s.confidence, 0)}</td>
              <td><EvidenceRefs ids={s.evidence_ids} data={data} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ screening */
function ScreeningTab({ data }: { data: CaseDetail }) {
  const det = (data.summary.details?.screening ?? {}) as Record<string, unknown>;
  const sanc = data.signals.filter((s) => s.signal_type.startsWith("sanctions"));
  const pep = data.signals.filter((s) => s.signal_type.startsWith("pep"));
  const lists = (det.lists ?? {}) as Record<string, number>;
  const fresh = (det.list_freshness ?? {}) as Record<string, { age_hours: number | null; records: number; stale: boolean }>;
  return (
    <div className="grid">
      <div className="alert alert-warn">
        Screening returns <strong>potential matches</strong> by fuzzy name and identifier comparison. A potential match is not a confirmed designation; a reviewer must
        confirm or discount it against the listed identifiers.
      </div>
      <div className="grid g2">
        <Card title={`Sanctions potential matches (${sanc.length})`} pad={false}><MatchTable signals={sanc} data={data} /></Card>
        <Card title={`PEP potential matches (${pep.length})`} pad={false}><MatchTable signals={pep} data={data} /></Card>
      </div>
      <div className="grid g2">
        <Card title="Names screened">
          <dl className="kv">
            <dt>Subject names</dt><dd>{((det.subject_names ?? []) as string[]).join(" · ") || "—"}</dd>
            <dt>Related parties</dt><dd>{String(det.related_screened ?? 0)} of {String(det.related_total ?? 0)} screened</dd>
            <dt>Review threshold</dt><dd>{String(det.threshold ?? "—")}</dd>
          </dl>
        </Card>
        <Card title="Lists used (point-in-time)" pad={false}>
          <table>
            <thead><tr><th>List</th><th className="num">Records</th><th>Age</th><th>State</th></tr></thead>
            <tbody>
              {Object.entries(fresh).map(([k, f]) => (
                <tr key={k}><td>{k}</td><td className="num">{lists[k] ?? f.records}</td><td>{f.age_hours == null ? "not loaded" : `${f.age_hours.toFixed(1)} h`}</td><td>{f.stale ? <span className="badge b-high">stale</span> : f.records ? <span className="badge b-low">current</span> : <span className="badge b-info">not loaded</span>}</td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}

function MatchTable({ signals, data }: { signals: SignalItem[]; data: CaseDetail }) {
  if (!signals.length) return <Empty>No potential matches above threshold.</Empty>;
  return (
    <table>
      <thead><tr><th>Match</th><th>List</th><th>Role</th><th className="num">Score</th><th>Evidence</th></tr></thead>
      <tbody>
        {signals.map((s) => {
          const d = s.detail as Record<string, unknown>;
          return (
            <tr key={s.id}>
              <td>{s.label}<div className="small muted">{((d.programs ?? []) as string[]).join(", ")} · method {String(d.method ?? "")}</div></td>
              <td>{String(d.source ?? "")}</td>
              <td>{String(d.role ?? "")}</td>
              <td className="num">{fmt.num(s.value)}</td>
              <td><EvidenceRefs ids={s.evidence_ids} data={data} /></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/* ------------------------------------------------------------------ ownership */
function OwnershipTab({ data }: { data: CaseDetail }) {
  const g = (data.graph ?? {}) as Record<string, unknown>;
  const reg = (data.summary.details?.corporate_registry ?? {}) as Record<string, unknown>;
  const ins = ((data.summary.details?.insiders ?? {}) as { insiders?: { name: string; roles: string[]; title: string | null }[] }).insiders ?? [];
  const paths = (g.exposure_paths ?? []) as { target: string; hops: number; path: string[]; flag: string; edges: { from: string; to: string; relation: string; evidence_id: string }[] }[];
  const [sel, setSel] = useState<string | null>(null);
  const selEdges = (data.graph_ui?.edges ?? []).filter((e) => sel && (e.source === sel || e.target === sel));
  return (
    <div className="grid">
      <div className="grid g-main">
        <Card title="Relationship graph (GraphRAG)" actions={<span className="muted small">{String(g.nodes ?? 0)} nodes · {String(g.edges ?? 0)} edges{data.graph_ui?.truncated && " · truncated"}</span>}>
          <RelationshipGraph nodes={data.graph_ui?.nodes ?? []} edges={data.graph_ui?.edges ?? []} onSelect={setSel} />
          {sel && (
            <div className="section-gap">
              <strong>{sel}</strong>
              <ul className="list-plain small">
                {selEdges.map((e, i) => <li key={i}>{e.source} <strong>{e.relation}</strong> {e.target} {e.basis && <span className="muted">— {e.basis}</span>} {e.evidence_id && <span className="cite">{e.evidence_id.slice(0, 12)}</span>}</li>)}
              </ul>
            </div>
          )}
        </Card>
        <div className="grid">
          <Card title="Ownership (GLEIF Level 2)">
            <dl className="kv">
              <dt>LEI status</dt><dd>{showValue(reg.lei_status)}</dd>
              <dt>Direct parent</dt><dd>{reg.direct_parent ? showValue(reg.direct_parent) : <span className="muted">none reported / exception</span>}</dd>
              <dt>Ultimate parent</dt><dd>{reg.ultimate_parent ? showValue(reg.ultimate_parent) : <span className="muted">none reported / exception</span>}</dd>
              <dt>GLEIF children</dt><dd>{showValue(reg.gleif_children)}</dd>
              <dt>Exhibit 21 subsidiaries</dt><dd>{String(reg.exhibit21_subsidiaries ?? "—")}</dd>
            </dl>
            <div className="disclaimer">
              GLEIF reports accounting-consolidation parents, and Exhibit 21 lists significant subsidiaries. Neither is a beneficial-ownership (UBO) register for natural
              persons; insiders below are Form 3/4 reporting persons.
            </div>
          </Card>
          <Card title={`Exposure paths (${paths.length})`}>
            {paths.length === 0 ? <Empty>No flagged node within 3 hops.</Empty> : paths.map((p, i) => (
              <div key={i} style={{ marginBottom: 10 }}>
                <span className={`badge ${p.flag === "sanctions" ? "b-high" : "b-medium"}`}>{p.flag}</span> <strong>{p.target}</strong> <span className="muted small">{p.hops} hop(s)</span>
                <div className="small">{p.edges.map((e, j) => <span key={j}>{e.from} → <em>{e.relation}</em> → {e.to} <span className="cite">{(e.evidence_id ?? "").slice(0, 12)}</span>{" "}</span>)}</div>
              </div>
            ))}
          </Card>
        </div>
      </div>
      <div className="grid g2">
        <Card title={`Insiders (SEC Form 3/4) — ${ins.length}`} pad={false}>
          {ins.length === 0 ? <Empty>No insider filings found.</Empty> : (
            <table><thead><tr><th>Name</th><th>Roles</th><th>Title</th></tr></thead>
              <tbody>{ins.map((x) => <tr key={x.name}><td>{x.name}</td><td>{x.roles.join(", ")}</td><td>{x.title ?? "—"}</td></tr>)}</tbody>
            </table>
          )}
        </Card>
        <Card title="Graph facts used to ground the narrative">
          {(data.graph_facts ?? []).length === 0 ? <Empty>None.</Empty> : <ul className="list-plain small" style={{ maxHeight: 420, overflowY: "auto" }}>{data.graph_facts!.slice(0, 40).map((f, i) => <li key={i}>{f}</li>)}</ul>}
        </Card>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ media */
function MediaTab({ data }: { data: CaseDetail }) {
  const det = (data.summary.details?.adverse_media ?? {}) as Record<string, unknown>;
  const sigs = data.signals.filter((s) => s.signal_type.startsWith("adverse_media"));
  const map = evidenceMap(data);
  const roles = (det.subject_roles ?? {}) as Record<string, number>;
  return (
    <div className="grid">
      <div className="grid g3">
        <Card title="Retrieval">
          <dl className="kv">
            {Object.entries((det.sources ?? {}) as Record<string, string>).map(([k, v]) => (
              <Fragment key={k}><dt>{k}</dt><dd><CoverageBadge status={v === "ok" ? "ok" : "unavailable"} /> <span className="small muted">query “{String(((det.queried ?? {}) as Record<string, string>)[k] ?? "")}”</span></dd></Fragment>
            ))}
            <dt>Items retrieved</dt><dd>{String(det.items_retrieved ?? 0)}</dd>
            <dt>Flagged</dt><dd>{String(det.flagged ?? 0)}</dd>
          </dl>
        </Card>
        <Card title="Subject role (who the article is about)">
          <dl className="kv">
            {Object.entries(roles).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
          </dl>
          <div className="disclaimer">Only items where the subject is the alleged wrongdoer score fully; victims and passing mentions are down-weighted.</div>
        </Card>
        <Card title="Method">
          <p className="small" style={{ margin: 0 }}>Keyword taxonomy + subject-role classifier over titles/abstracts of DOJ press releases and GDELT articles. No LLM generates or rewrites media findings.</p>
        </Card>
      </div>
      <Card title={`Adverse media signals (${sigs.length})`} pad={false}>
        {sigs.length === 0 ? <Empty>No adverse-media signals.</Empty> : (
          <table>
            <thead><tr><th>Item</th><th>Role</th><th>Categories</th><th>Severity</th><th>Source</th></tr></thead>
            <tbody>
              {sigs.map((s) => {
                const d = s.detail as Record<string, unknown>;
                const ev = map.get(s.evidence_ids[0] ?? "");
                return (
                  <tr key={s.id}>
                    <td>{ev ? <a href={ev.source_url} target="_blank" rel="noreferrer noopener">{ev.title}</a> : s.label}<div className="small muted">{ev ? fmt.date(String(ev.structured.published ?? ev.retrieved_at)) : ""} · matched: {((d.matched_terms ?? []) as string[]).join(", ")}</div></td>
                    <td><span className={`badge ${d.subject_role === "subject" ? "b-high" : "b-info"}`}>{String(d.subject_role)}</span></td>
                    <td className="small">{((d.categories ?? []) as string[]).join(", ")}</td>
                    <td><SeverityBadge severity={s.severity} /></td>
                    <td>{String(d.source ?? "")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ financials */
function FinancialsTab({ data }: { data: CaseDetail }) {
  const ind = (((data.summary.details?.financials ?? {}) as Record<string, unknown>).indicators ?? null) as Record<string, unknown> | null;
  const peer = (data.summary.peer ?? {}) as Record<string, unknown>;
  const fin = data.evidence.find((e) => e.kind === "financial_facts");
  if (!ind) return <Empty>No XBRL financial data for this entity (e.g. not an SEC registrant).</Empty>;
  const money = ["revenue", "net_income", "total_assets", "total_liabilities", "equity"];
  const ratios = ["current_ratio", "liabilities_to_assets", "net_margin", "return_on_assets", "revenue_growth"];
  const pct = (peer.percentiles ?? {}) as Record<string, number>;
  return (
    <div className="grid g2">
      <Card title={`Reported figures (SEC XBRL, period ending ${String(ind.period_end ?? "—")})`}>
        <dl className="kv">
          {money.map((k) => <Fragment key={k}><dt>{fmt.label(k)}</dt><dd className="num">{fmt.money(ind[k])}</dd></Fragment>)}
          {ratios.map((k) => <Fragment key={k}><dt>{fmt.label(k)}</dt><dd className="num">{k === "current_ratio" ? fmt.num(ind[k]) : fmt.pct(ind[k])}</dd></Fragment>)}
          <dt>Rule flags</dt><dd>{((ind.flags ?? []) as string[]).join("; ") || "none"}</dd>
          <dt>Missing concepts</dt><dd>{((ind.missing ?? []) as string[]).join(", ") || "none"}</dd>
        </dl>
        {fin && <div className="disclaimer">Source: <a href={fin.source_url} target="_blank" rel="noreferrer noopener">{fin.source_url}</a> · retrieved {fmt.date(fin.retrieved_at)}</div>}
      </Card>
      <Card title="Peer comparison (IsolationForest on SEC XBRL frames)">
        {!peer.available ? <div className="alert alert-info">{String(peer.reason ?? "Peer model unavailable.")}</div> : (
          <>
            <p>
              {peer.anomaly ? <span className="badge b-medium">statistical outlier</span> : <span className="badge b-low">within peer distribution</span>}{" "}
              <span className="muted small">score {fmt.num(peer.score, 3)} · {String(peer.n_peers)} peers · period {String(peer.model_period)}</span>
            </p>
            <table>
              <thead><tr><th>Feature</th><th className="num">Value</th><th>Peer percentile</th></tr></thead>
              <tbody>
                {Object.entries(pct).map(([k, v]) => (
                  <tr key={k}><td>{k}</td><td className="num">{fmt.num(((peer.values ?? {}) as Record<string, number>)[k], 3)}</td><td style={{ minWidth: 160 }}><Bar value={v} max={100} color={v <= 2 || v >= 98 ? "var(--medium)" : "var(--accent)"} /> <span className="small">{v}</span></td></tr>
                ))}
              </tbody>
            </table>
            <div className="disclaimer">An outlier is a statistical observation for analyst attention, not evidence of distress or misconduct.</div>
          </>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ report */
function ReportTab({ data }: { data: CaseDetail }) {
  return (
    <div className="grid g-main">
      <Card title="Due-diligence memo" actions={data.report_grounding != null && <span className={`badge ${data.report_grounding >= 0.8 ? "b-low" : "b-medium"}`}>grounding {fmt.pct(data.report_grounding, 0)}</span>}>
        {data.report ? <div className="md" data-testid="report"><Markdown source={data.report} /></div> : <Empty>The report is produced at the end of the investigation.</Empty>}
      </Card>
      <Card title="Research Q&amp;A (cited)">
        {data.research.length === 0 ? <Empty>No research questions answered.</Empty> : data.research.map((r, i) => (
          <div key={i} style={{ marginBottom: 14 }}>
            <strong className="small">{r.question}</strong>
            <div className="small md"><Markdown source={r.answer} /></div>
            <div className="small muted">grounding {fmt.pct(r.grounding_score, 0)} · {r.citations.length} citation(s)</div>
          </div>
        ))}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ trace */
function TraceTab({ data }: { data: CaseDetail }) {
  const total = data.events.reduce((a, e) => a + (e.duration_ms || 0), 0);
  return (
    <div className="grid g-main">
      <Card title={`Agent execution trace (${data.events.length} steps · ${(total / 1000).toFixed(1)} s)`}>
        {data.events.length === 0 ? <Empty>No steps recorded yet.</Empty> : (
          <ul className="timeline" data-testid="trace">
            {data.events.map((e, i) => (
              <li key={i}>
                <span className="tick" style={{ background: e.status === "completed" ? "var(--low)" : e.status === "failed" ? "var(--high)" : "var(--medium)" }} />
                <span><strong>{fmt.label(e.node)}</strong><div className="small muted">{fmt.date(e.started_at)}</div></span>
                <span className="num small">{e.duration_ms} ms</span>
                <span className="small wrap">
                  <span className={`badge ${e.status === "completed" ? "b-low" : e.status === "failed" ? "b-high" : "b-medium"}`}>{e.status}</span>{" "}
                  {e.error && <span style={{ color: "var(--high)" }}>{e.error} </span>}
                  <span className="muted">{JSON.stringify(e.detail).slice(0, 300)}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="LLM usage (this case)">
        <dl className="kv">
          <dt>Calls</dt><dd>{data.llm_usage.calls}</dd>
          <dt>Input tokens</dt><dd className="num">{data.llm_usage.input_tokens.toLocaleString()}</dd>
          <dt>Output tokens</dt><dd className="num">{data.llm_usage.output_tokens.toLocaleString()}</dd>
          <dt>Cost (USD)</dt><dd className="num">${data.llm_usage.cost_usd.toFixed(4)}</dd>
        </dl>
        <div className="disclaimer">The LLM may only summarise retrieved evidence with citations. Ungrounded output is rejected and replaced by the extractive fallback.</div>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ case audit */
function CaseAuditTab({ caseId }: { caseId: string }) {
  const { can } = useAuth();
  const { data, error } = useLoad(() => (can("audit:read") ? api<AuditRecord[]>(`/api/v1/audit?resource_id=${encodeURIComponent(caseId)}`) : Promise.resolve(null)), [caseId]);
  if (!can("audit:read")) return <Empty>Viewing the audit trail requires the audit:read permission.</Empty>;
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <Card title={`Immutable audit records for ${caseId}`} pad={false}>
      <table>
        <thead><tr><th className="num">Seq</th><th>Time</th><th>Actor</th><th>Action</th><th>Details</th><th>Hash</th></tr></thead>
        <tbody>
          {data.map((r) => (
            <tr key={r.seq}><td className="num">{r.seq}</td><td className="nowrap">{fmt.date(r.ts)}</td><td>{r.actor}<div className="small muted">{r.actor_roles.join(", ")}</div></td><td>{r.action}</td><td className="small wrap">{JSON.stringify(r.details).slice(0, 240)}</td><td className="cite">{r.hash.slice(0, 12)}</td></tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
