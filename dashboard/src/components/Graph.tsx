import type { GraphEdge, GraphNode } from "../types";

/** Deterministic radial layout: subject at the centre, direct relations on ring 1, the rest on ring 2. */
export function RelationshipGraph({ nodes, edges, onSelect }: { nodes: GraphNode[]; edges: GraphEdge[]; onSelect?: (id: string) => void }) {
  if (!nodes.length) return <div className="empty">No evidence-backed relationships were found.</div>;
  const W = 1100;
  const H = 720;
  const cx = W / 2;
  const cy = H / 2;
  const subject = nodes.find((n) => n.subject) ?? nodes[0];
  const direct = new Set<string>();
  edges.forEach((e) => {
    if (e.source === subject.id) direct.add(e.target);
    if (e.target === subject.id) direct.add(e.source);
  });
  const ring1 = nodes.filter((n) => n.id !== subject.id && direct.has(n.id));
  const ring2 = nodes.filter((n) => n.id !== subject.id && !direct.has(n.id));
  const pos = new Map<string, [number, number]>([[subject.id, [cx, cy]]]);
  const place = (list: GraphNode[], r: number, offset: number) =>
    list.forEach((n, i) => {
      const a = (2 * Math.PI * i) / Math.max(list.length, 1) + offset;
      pos.set(n.id, [cx + r * Math.cos(a), cy + r * 0.82 * Math.sin(a)]);
    });
  place(ring1, Math.min(260, 110 + ring1.length * 5), -Math.PI / 2);
  place(ring2, 320, -Math.PI / 2 + 0.2);
  const color = (n: GraphNode) => (n.sanctioned ? "var(--high)" : n.pep ? "var(--medium)" : n.subject ? "var(--accent)" : n.kind === "person" || n.kind === "individual" ? "#7a5af8" : "#64748b");
  const edgeColor = (r: string) => (r === "INSIDER_OF" ? "#c4b5fd" : r.startsWith("CONSOLIDATES") ? "#93c5fd" : "#cbd5e1");
  return (
    <div className="graph">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Relationship graph">
        {edges.map((e, i) => {
          const a = pos.get(e.source);
          const b = pos.get(e.target);
          if (!a || !b) return null;
          return (
            <line key={i} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={edgeColor(e.relation)} strokeWidth={1.4}>
              <title>{`${e.source} ${e.relation} ${e.target}${e.basis ? ` — ${e.basis}` : ""}`}</title>
            </line>
          );
        })}
        {nodes.map((n) => {
          const p = pos.get(n.id);
          if (!p) return null;
          const r = n.subject ? 13 : n.sanctioned || n.pep ? 9 : 6;
          const label = n.id.length > 30 ? `${n.id.slice(0, 28)}…` : n.id;
          const dx = p[0] - cx;
          const outward = !n.subject && Math.abs(dx) > 20;
          const anchor = n.subject ? "middle" : outward ? (dx > 0 ? "start" : "end") : "middle";
          const tx = outward ? p[0] + (dx > 0 ? r + 4 : -r - 4) : p[0];
          const ty = outward ? p[1] + 3 : p[1] + (p[1] < cy ? -r - 4 : r + 12);
          return (
            <g key={n.id} onClick={() => onSelect?.(n.id)} style={{ cursor: onSelect ? "pointer" : "default" }}>
              <circle cx={p[0]} cy={p[1]} r={r} fill={color(n)} stroke="#fff" strokeWidth={2} />
              <text x={tx} y={n.subject ? p[1] + r + 14 : ty} textAnchor={anchor} fontSize={n.subject ? 15 : 12} fontWeight={n.subject ? 700 : 400} fill="#334155">
                {label}
              </text>
              <title>{`${n.id}${n.sanctioned ? " — sanctions potential match" : ""}${n.pep ? " — PEP potential match" : ""}`}</title>
            </g>
          );
        })}
      </svg>
      <div className="legend">
        <span><i className="dot" style={{ background: "var(--accent)" }} /> Subject</span>
        <span><i className="dot" style={{ background: "#64748b" }} /> Company</span>
        <span><i className="dot" style={{ background: "#7a5af8" }} /> Person (insider)</span>
        <span><i className="dot" style={{ background: "var(--high)" }} /> Sanctions potential match</span>
        <span><i className="dot" style={{ background: "var(--medium)" }} /> PEP potential match</span>
        <span style={{ color: "#3b82f6" }}>— GLEIF consolidation</span>
        <span style={{ color: "#a78bfa" }}>— Form 3/4 insider</span>
        <span style={{ color: "#94a3b8" }}>— Exhibit 21 subsidiary</span>
      </div>
    </div>
  );
}
