import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmt } from "../api";
import { BandBadge, Card, Empty, ErrorBox, Loading, OutcomeBadge, StatusBadge, useLoad } from "../components/ui";
import type { CaseSummary } from "../types";

const PAGE = 25;

export function CasesPage() {
  const nav = useNavigate();
  const [status, setStatus] = useState("");
  const [band, setBand] = useState("");
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (status) params.set("status", status);
  if (band) params.set("band", band);
  if (applied) params.set("q", applied);
  const { data, error } = useLoad(
    () => api<{ total: number; items: CaseSummary[] }>(`/api/v1/cases?${params}`),
    [params.toString()],
    (d) => (d?.items.some((c) => c.status === "running" || c.status === "queued") ? 4000 : null),
  );
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Case explorer</h1>
          <p>Every investigation you are authorised to see, newest first.</p>
        </div>
      </div>
      <Card pad={false} title={
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setApplied(q.trim()); setOffset(0); }}>
          <div className="field">
            <label htmlFor="cq">Search entity</label>
            <input id="cq" value={q} onChange={(e) => setQ(e.target.value)} placeholder="name contains…" />
          </div>
          <div className="field">
            <label htmlFor="cs">Status</label>
            <select id="cs" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
              <option value="">All</option>
              {["queued", "running", "awaiting_review", "approved", "rejected", "cleared", "failed"].map((s) => <option key={s} value={s}>{fmt.label(s)}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="cb">Band</label>
            <select id="cb" value={band} onChange={(e) => { setBand(e.target.value); setOffset(0); }}>
              <option value="">All</option>
              <option>HIGH</option><option>MEDIUM</option><option>LOW</option>
            </select>
          </div>
          <button className="btn" type="submit">Apply</button>
        </form>
      }>
        <ErrorBox error={error} />
        {!data ? <Loading /> : data.items.length === 0 ? <Empty>No cases match these filters.</Empty> : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Entity</th><th>Case</th><th>Status</th><th>Band</th><th className="num">Score</th><th>Policy</th><th>Analyst</th><th>Opened</th></tr>
                </thead>
                <tbody>
                  {data.items.map((c) => (
                    <tr key={c.case_id} className="clickable" onClick={() => nav(`/cases/${c.case_id}`)}>
                      <td><strong>{c.resolved_name ?? c.entity_query}</strong>{c.resolved_name && c.resolved_name !== c.entity_query && <div className="muted small">query: {c.entity_query}</div>}</td>
                      <td className="muted small nowrap">{c.case_id}</td>
                      <td><StatusBadge status={c.status} /></td>
                      <td><BandBadge band={c.risk_band} /></td>
                      <td className="num">{c.risk_score ?? "—"}</td>
                      <td><OutcomeBadge outcome={c.policy_outcome} /></td>
                      <td>{c.created_by}</td>
                      <td className="nowrap">{fmt.date(c.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ padding: 12, justifyContent: "space-between" }}>
              <span className="muted small">{offset + 1}–{offset + data.items.length} of {data.total}</span>
              <span className="toolbar">
                <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
                <button className="btn" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
              </span>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
