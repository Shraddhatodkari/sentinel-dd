import { useState } from "react";
import { api, fmt } from "../api";
import { useAuth } from "../auth";
import { Card, ErrorBox } from "../components/ui";
import type { CaseDetail } from "../types";

export function ReviewPanel({ data, onDecided }: { data: CaseDetail; onDecided: () => void }) {
  const { session, can } = useAuth();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const rv = data.review;
  if (!rv) {
    return (
      <Card title="Human decision">
        <p className="muted small" style={{ margin: 0 }}>
          {data.status === "cleared" ? "Auto-cleared by policy (auto-clear enabled and no rule required review)." : "No review has been requested yet."}
        </p>
      </Card>
    );
  }
  if (rv.status !== "pending") {
    return (
      <Card title="Human decision">
        <div data-testid="decision-record">
          <span className={`badge ${rv.status === "approved" ? "b-low" : "b-high"}`}>{rv.status.toUpperCase()}</span>
          <dl className="kv section-gap">
            <dt>Reviewer</dt><dd>{rv.decided_by} ({rv.decided_role})</dd>
            <dt>Decided</dt><dd>{fmt.date(rv.decided_at)}</dd>
            <dt>Tier</dt><dd>{rv.tier}</dd>
            <dt>Rationale</dt><dd>{rv.comment}</dd>
          </dl>
          <div className="disclaimer">Decisions are final and recorded in the hash-chained audit log.</div>
        </div>
      </Card>
    );
  }
  const isCreator = session?.username === rv.requested_by || session?.username === data.created_by;
  const needsSenior = rv.tier === "ESCALATE" && !can("review:decide_escalated");
  const allowed = can("review:decide") && !isCreator && !needsSenior;
  const decide = async (approve: boolean) => {
    setBusy(true);
    setError(undefined);
    try {
      await api(`/api/v1/cases/${data.case_id}/decision`, { method: "POST", json: { approve, comment: comment.trim() } });
      setComment("");
      onDecided();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title={<>Human review <span className={`badge ${rv.tier === "ESCALATE" ? "b-high" : "b-medium"}`}>{rv.tier}</span></>}>
      <div className="small" style={{ marginBottom: 10 }}>
        Requires <strong>{rv.required_role}</strong> · requested {fmt.ago(rv.requested_at)} by {rv.requested_by}
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          {rv.reasons.map((r) => <li key={r.rule_id}><strong>{r.rule_id}</strong> — {r.description}</li>)}
        </ul>
      </div>
      {!allowed ? (
        <div className="alert alert-info small" data-testid="review-blocked">
          {!can("review:decide")
            ? "Your role cannot record review decisions."
            : isCreator
              ? "Segregation of duties: you opened this case, so a different reviewer must decide it."
              : "Escalated cases require a senior reviewer."}
        </div>
      ) : (
        <>
          <div className="field">
            <label htmlFor="rationale">Decision rationale (required, min. 10 characters)</label>
            <textarea id="rationale" rows={4} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={4000} placeholder="State which evidence you relied on and why potential matches were confirmed or discounted." />
          </div>
          <ErrorBox error={error} />
          <div className="toolbar" style={{ marginTop: 8 }}>
            <button className="btn btn-success" disabled={busy || comment.trim().length < 10} onClick={() => decide(true)}>Approve relationship</button>
            <button className="btn btn-danger" disabled={busy || comment.trim().length < 10} onClick={() => decide(false)}>Reject relationship</button>
          </div>
        </>
      )}
    </Card>
  );
}
