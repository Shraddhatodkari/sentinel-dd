import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmt } from "../api";
import { BandBadge, Card, Empty, ErrorBox, Loading, useLoad } from "../components/ui";
import type { Review } from "../types";

export function ReviewQueuePage() {
  const nav = useNavigate();
  const [status, setStatus] = useState<"pending" | "approved" | "rejected">("pending");
  const { data, error } = useLoad(() => api<Review[]>(`/api/v1/reviews?status=${status}`), [status], () => 15000);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Human review queue</h1>
          <p>Maker-checker: the analyst who opened a case cannot decide it; escalated cases need a senior reviewer.</p>
        </div>
        <div className="tabs" style={{ marginBottom: 0, borderBottom: "none" }}>
          {(["pending", "approved", "rejected"] as const).map((s) => (
            <button key={s} className={status === s ? "active" : ""} onClick={() => setStatus(s)}>{fmt.label(s)}</button>
          ))}
        </div>
      </div>
      <Card pad={false}>
        <ErrorBox error={error} />
        {!data ? <Loading /> : data.length === 0 ? <Empty>No {status} reviews.</Empty> : (
          <div className="table-wrap">
            <table data-testid="review-table">
              <thead>
                <tr><th>Entity</th><th>Tier</th><th>Band</th><th className="num">Score</th><th>Reasons</th><th>Requested</th>{status === "pending" ? <th>Your action</th> : <><th>Decided by</th><th>Rationale</th></>}</tr>
              </thead>
              <tbody>
                {data.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => nav(`/cases/${r.case_id}`)}>
                    <td><strong>{r.entity}</strong><div className="muted small">{r.case_id}</div></td>
                    <td><span className={`badge ${r.tier === "ESCALATE" ? "b-high" : "b-medium"}`}>{r.tier}</span></td>
                    <td><BandBadge band={r.risk_band} /></td>
                    <td className="num">{r.risk_score ?? "—"}</td>
                    <td className="small">{r.reasons.map((x) => x.rule_id).join(", ")}</td>
                    <td className="nowrap">{fmt.ago(r.requested_at)}<div className="small muted">by {r.requested_by}</div></td>
                    {status === "pending" ? (
                      <td>{r.can_decide ? <span className="badge b-accent">ready to decide</span> : <span className="badge b-info">not eligible</span>}</td>
                    ) : (
                      <><td>{r.decided_by}<div className="small muted">{r.decided_role} · {fmt.date(r.decided_at)}</div></td><td className="small">{r.comment}</td></>
                    )}
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
