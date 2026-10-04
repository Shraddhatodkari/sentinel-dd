import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Card, ErrorBox } from "../components/ui";
import type { CaseSummary } from "../types";

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

export function NewInvestigationPage() {
  const nav = useNavigate();
  const [query, setQuery] = useState("");
  const [cik, setCik] = useState("");
  const [lei, setLei] = useState("");
  const [ticker, setTicker] = useState("");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  // One idempotency key per form instance: a double-submit or network retry cannot open two cases.
  const [idemKey] = useState(newKey);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const body: Record<string, unknown> = { query: query.trim() };
      if (cik.trim()) body.cik = Number(cik.trim());
      if (lei.trim()) body.lei = lei.trim().toUpperCase();
      if (ticker.trim()) body.ticker = ticker.trim().toUpperCase();
      if (question.trim()) body.question = question.trim();
      const c = await api<CaseSummary>("/api/v1/cases", { method: "POST", json: body, headers: { "Idempotency-Key": idemKey } });
      nav(`/cases/${c.case_id}`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>New investigation</h1>
          <p>Sentinel resolves the entity against SEC EDGAR and GLEIF, then collects evidence from the authoritative sources listed below.</p>
        </div>
      </div>
      <div className="grid g-main">
        <Card title="Subject">
          <form onSubmit={submit} aria-label="New investigation">
            <div className="field">
              <label htmlFor="q">Legal entity name</label>
              <input id="q" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. The Boeing Company" required minLength={2} maxLength={200} />
              <div className="hint">The name is screened, so use the registered legal name where known.</div>
            </div>
            <div className="grid g3">
              <div className="field">
                <label htmlFor="cik">SEC CIK (optional)</label>
                <input id="cik" inputMode="numeric" pattern="[0-9]{1,10}" value={cik} onChange={(e) => setCik(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="lei">LEI (optional)</label>
                <input id="lei" pattern="[A-Za-z0-9]{20}" value={lei} onChange={(e) => setLei(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="ticker">Ticker (optional)</label>
                <input id="ticker" maxLength={10} value={ticker} onChange={(e) => setTicker(e.target.value)} />
              </div>
            </div>
            <div className="field">
              <label htmlFor="question">Specific research question (optional)</label>
              <textarea id="question" rows={3} maxLength={500} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="e.g. Does the company disclose export-control investigations?" />
              <div className="hint">Answered only from retrieved evidence with citations; unanswerable questions return INSUFFICIENT_EVIDENCE.</div>
            </div>
            <ErrorBox error={error} />
            <div className="toolbar" style={{ marginTop: 12 }}>
              <button className="btn btn-primary" type="submit" disabled={busy || query.trim().length < 2}>
                {busy ? "Opening case…" : "Start investigation"}
              </button>
            </div>
          </form>
        </Card>
        <Card title="What happens next">
          <ol style={{ paddingLeft: 18, margin: 0, lineHeight: 1.7 }}>
            <li><strong>Resolve</strong> — SEC EDGAR company index and GLEIF LEI register.</li>
            <li><strong>Collect in parallel</strong> — sanctions/PEP screening (OFAC, UN, UK, EU, US Congress), GLEIF ownership, Exhibit 21 subsidiaries, Form 3/4 insiders, 10-K sections, XBRL financials, DOJ press releases and GDELT.</li>
            <li><strong>Analyse</strong> — case GraphRAG, signals, versioned scorecard and peer model.</li>
            <li><strong>Policy</strong> — deterministic rules decide CLEAR / REVIEW / ESCALATE.</li>
            <li><strong>Human review</strong> — a different, authorised reviewer records the decision.</li>
          </ol>
          <div className="alert alert-info" style={{ marginTop: 14 }}>
            Investigations take seconds to a few minutes depending on source latency. Progress is shown live on the case page.
          </div>
        </Card>
      </div>
    </>
  );
}
