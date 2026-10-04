import { useState } from "react";
import { api, fmt } from "../api";
import { Card, Empty, ErrorBox, Loading, useLoad } from "../components/ui";
import type { AuditRecord } from "../types";

interface Verify { intact: boolean; records: number; first_bad_seq?: number | null; reason?: string | null; [k: string]: unknown }

export function AuditPage() {
  const [resource, setResource] = useState("");
  const [applied, setApplied] = useState("");
  const [before, setBefore] = useState<number | undefined>();
  const params = new URLSearchParams({ limit: "100" });
  if (applied) params.set("resource_id", applied);
  if (before) params.set("before", String(before));
  const { data, error } = useLoad(() => api<AuditRecord[]>(`/api/v1/audit?${params}`), [params.toString()]);
  const [verify, setVerify] = useState<Verify | null>(null);
  const [vErr, setVErr] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const runVerify = async () => {
    setBusy(true);
    setVErr(undefined);
    try {
      setVerify(await api<Verify>("/api/v1/audit/verify"));
    } catch (e) {
      setVErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Audit trail</h1>
          <p>Append-only, hash-chained record of every security-relevant action. Database triggers reject UPDATE and DELETE.</p>
        </div>
        <button className="btn btn-primary" onClick={runVerify} disabled={busy}>{busy ? "Verifying…" : "Verify chain integrity"}</button>
      </div>
      <ErrorBox error={vErr} />
      {verify && (
        <div className={`alert ${verify.intact ? "alert-ok" : "alert-error"}`} style={{ marginBottom: 16 }} data-testid="verify-result">
          {verify.intact ? <strong>Chain intact.</strong> : <strong>Chain BROKEN.</strong>}{" "}
          {Object.entries(verify).filter(([k]) => k !== "intact").map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}
        </div>
      )}
      <Card pad={false} title={
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setApplied(resource.trim()); setBefore(undefined); }}>
          <div className="field"><label htmlFor="ar">Resource ID (case, user, source…)</label><input id="ar" value={resource} onChange={(e) => setResource(e.target.value)} /></div>
          <button className="btn" type="submit">Filter</button>
        </form>
      }>
        <ErrorBox error={error} />
        {!data ? <Loading /> : data.length === 0 ? <Empty>No records.</Empty> : (
          <>
            <div className="table-wrap">
              <table data-testid="audit-table">
                <thead><tr><th className="num">Seq</th><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Resource</th><th>Details</th><th>Request</th><th>Hash ← prev</th></tr></thead>
                <tbody>
                  {data.map((r) => (
                    <tr key={r.seq}>
                      <td className="num">{r.seq}</td>
                      <td className="nowrap small">{fmt.date(r.ts)}</td>
                      <td>{r.actor}<div className="small muted">{r.actor_roles.join(", ")}</div></td>
                      <td><strong>{r.action}</strong></td>
                      <td className="small">{r.resource_type}<div className="muted wrap">{r.resource_id}</div></td>
                      <td className="small wrap" style={{ maxWidth: 360 }}>{JSON.stringify(r.details).slice(0, 300)}</td>
                      <td className="small muted">{(r.request_id || "").slice(0, 8)}</td>
                      <td><span className="cite">{r.hash.slice(0, 10)}</span><div className="small muted">← {r.prev_hash.slice(0, 10)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ padding: 12, justifyContent: "flex-end" }}>
              {before && <button className="btn" onClick={() => setBefore(undefined)}>Newest</button>}
              <button className="btn" disabled={data.length < 100} onClick={() => setBefore(data[data.length - 1].seq)}>Older</button>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
