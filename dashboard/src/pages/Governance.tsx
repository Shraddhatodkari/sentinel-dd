import { Fragment } from "react";
import { api } from "../api";
import { Card, ErrorBox, Loading, useLoad } from "../components/ui";

interface AgentSpec {
  name: string;
  purpose: string;
  inputs: string[];
  outputs: string[];
  tools: string[];
  permissions: string[];
  failure_modes: string[];
  guardrails: string[];
  metrics: string[];
  fallback: string;
  escalation: string;
}

interface Governance {
  policy_version: string;
  scorecard_version: string;
  prompt_version: string;
  media_taxonomy: string;
  screening_method: string;
  auto_clear_enabled: boolean;
  screening_threshold: number;
  grounding_threshold: number;
  jurisdiction_policy: Record<string, unknown>;
  llm: { provider: string; model: string };
  peer_model: string | null;
  agents: AgentSpec[];
  sources: { id: string; name: string; authority: string; license: string; kind: string }[];
}

const List = ({ items }: { items: string[] }) => (items.length ? <ul style={{ margin: 0, paddingLeft: 16 }}>{items.map((x, i) => <li key={i}>{x}</li>)}</ul> : <span className="muted">—</span>);

export function GovernancePage() {
  const { data, error } = useLoad(() => api<Governance>("/api/v1/governance"), []);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Governance &amp; model inventory</h1>
          <p>Versions recorded with every assessment so any decision can be reproduced. Thresholds are configuration, not code.</p>
        </div>
      </div>
      <div className="grid g3">
        <Card title="Decision components">
          <dl className="kv" data-testid="versions">
            <dt>Policy</dt><dd>{data.policy_version}</dd>
            <dt>Scorecard</dt><dd>{data.scorecard_version}</dd>
            <dt>Screening</dt><dd>{data.screening_method}</dd>
            <dt>Media taxonomy</dt><dd>{data.media_taxonomy}</dd>
            <dt>Prompts</dt><dd>{data.prompt_version}</dd>
            <dt>Peer model</dt><dd>{data.peer_model ?? <span className="muted">not trained</span>}</dd>
            <dt>LLM</dt><dd>{data.llm.provider} · {data.llm.model}</dd>
          </dl>
        </Card>
        <Card title="Thresholds">
          <dl className="kv">
            <dt>Screening review</dt><dd>{data.screening_threshold}</dd>
            <dt>Grounding minimum</dt><dd>{data.grounding_threshold}</dd>
            <dt>Risk bands</dt><dd>LOW &lt; 25 ≤ MEDIUM &lt; 50 ≤ HIGH</dd>
            <dt>Auto-clear</dt><dd>{data.auto_clear_enabled ? <span className="badge b-medium">enabled</span> : <><span className="badge b-low">disabled</span><div className="small muted">every case gets a human decision</div></>}</dd>
          </dl>
        </Card>
        <Card title="Jurisdiction policy">
          <pre className="small" style={{ margin: 0, whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto" }}>{JSON.stringify(data.jurisdiction_policy, null, 1)}</pre>
        </Card>
      </div>
      <div className="section-gap" />
      <Card title={`Agent contracts (${data.agents.length})`} pad={false}>
        <div className="table-wrap">
          <table data-testid="agents-table">
            <thead><tr><th>Agent</th><th>Inputs → outputs</th><th>Tools &amp; permissions</th><th>Failure modes / fallback</th><th>Guardrails</th><th>Metrics</th><th>Escalation</th></tr></thead>
            <tbody>
              {data.agents.map((a) => (
                <Fragment key={a.name}>
                  <tr>
                    <td style={{ minWidth: 160 }}><strong>{a.name}</strong><div className="small muted">{a.purpose}</div></td>
                    <td className="small"><List items={a.inputs} /><div className="muted">→</div><List items={a.outputs} /></td>
                    <td className="small"><List items={a.tools} /><div className="muted">perm:</div><List items={a.permissions} /></td>
                    <td className="small"><List items={a.failure_modes} /><div className="muted">fallback: {a.fallback}</div></td>
                    <td className="small"><List items={a.guardrails} /></td>
                    <td className="small"><List items={a.metrics} /></td>
                    <td className="small">{a.escalation}</td>
                  </tr>
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="section-gap" />
      <Card title="Source catalogue" pad={false}>
        <table>
          <thead><tr><th>ID</th><th>Name</th><th>Authority</th><th>Licence / terms</th><th>Kind</th></tr></thead>
          <tbody>{data.sources.map((s) => <tr key={s.id}><td>{s.id}</td><td>{s.name}</td><td className="small">{s.authority}</td><td className="small">{s.license}</td><td>{s.kind}</td></tr>)}</tbody>
        </table>
      </Card>
    </>
  );
}
