"""Knowledge graph with entity resolution and due-diligence graph analytics.

Analytics:
* effective (multiplicative) ownership and Ultimate Beneficial Owner (UBO) discovery
* sanctions / PEP exposure within *k* hops (with explainable paths)
* offshore layering depth (shell-company red flag)
* community detection + community summaries (Microsoft GraphRAG-style global context)
"""

from __future__ import annotations

import difflib
import re
import threading
from dataclasses import dataclass
from typing import Any

import networkx as nx

from sentinel.graphrag.extractor import Extraction

OFFSHORE = frozenset({"British Virgin Islands", "Cayman Islands", "Panama", "Seychelles", "Mauritius", "Cyprus"})
_SUFFIX = re.compile(
    r"\b(private limited|pvt\.? ltd\.?|ltd\.?|limited|inc\.?|incorporated|llc|plc|fze|gmbh|corp\.?|corporation|"
    r"co\.?|s\.?a\.?|ag|holdings?)\b",
    re.I,
)
_PUNCT = re.compile(r"[^a-z0-9 ]")


def normalize(name: str) -> str:
    n = _SUFFIX.sub(" ", name.lower())
    n = _PUNCT.sub(" ", n)
    return " ".join(n.split())


@dataclass
class ExposurePath:
    target: str
    hops: int
    path: list[str]
    reason: str


class KnowledgeGraph:
    def __init__(self, fuzzy_threshold: float = 0.92) -> None:
        self.g: nx.MultiDiGraph = nx.MultiDiGraph()
        self._alias: dict[str, str] = {}  # normalized -> canonical
        self.fuzzy_threshold = fuzzy_threshold
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ resolution
    def resolve(self, name: str, create: bool = True, kind: str = "company") -> str | None:
        key = normalize(name)
        if not key:
            return None
        with self._lock:
            if key in self._alias:
                return self._alias[key]
            match = difflib.get_close_matches(key, list(self._alias), n=1, cutoff=self.fuzzy_threshold)
            if match:
                self._alias[key] = self._alias[match[0]]
                return self._alias[key]
            if not create:
                return None
            self._alias[key] = name
            self.g.add_node(name, kind=kind, sources=set())
            return name

    def _infer_kind(self, name: str, default: str) -> str:
        if _SUFFIX.search(name):
            return "company"
        return default

    # ------------------------------------------------------------------ build
    def add(self, ex: Extraction) -> None:
        with self._lock:
            for f in ex.facts:
                node = self.resolve(f.name, kind=self._infer_kind(f.name, f.kind))
                if node is None:
                    continue
                data = self.g.nodes[node]
                if f.kind != "unknown" and data.get("kind") in (None, "unknown"):
                    data["kind"] = f.kind
                data.update(f.flags)
                for flag in ("sanctioned", "pep"):
                    if f.flags.get(flag):
                        data[f"{flag}_source"] = f.source
                data["sources"].add(f.source)
            for t in ex.triples:
                kind_s = "person" if t.relation == "OFFICER_OF" else self._infer_kind(t.subject, "unknown")
                kind_o = "country" if t.relation == "INCORPORATED_IN" else "company"
                s = self.resolve(t.subject, kind=kind_s)
                o = self.resolve(t.obj, kind=kind_o)
                if s is None or o is None or s == o:
                    continue
                if kind_s == "person" and self.g.nodes[s].get("kind") == "unknown":
                    self.g.nodes[s]["kind"] = "person"
                exists = any(d.get("relation") == t.relation for d in self.g.get_edge_data(s, o, default={}).values())
                if not exists:
                    self.g.add_edge(s, o, relation=t.relation, source=t.source, **t.attrs)
                if t.relation == "INCORPORATED_IN":
                    self.g.nodes[s]["jurisdiction"] = o

    def set_flag(self, name: str, **flags: Any) -> None:
        node = self.resolve(name)
        if node:
            self.g.nodes[node].update(flags)

    # ------------------------------------------------------------------ analytics
    def owners_of(self, entity: str) -> list[tuple[str, float, str]]:
        node = self.resolve(entity, create=False)
        if node is None:
            return []
        return [
            (u, float(d.get("pct", 0.0)), d.get("source", ""))
            for u, _, d in self.g.in_edges(node, data=True)
            if d.get("relation") == "OWNS"
        ]

    def ultimate_owners(self, entity: str, threshold: float = 25.0, max_depth: int = 6) -> list[dict[str, Any]]:
        """Walk ownership upward multiplying stakes; return terminal owners >= threshold %."""
        start = self.resolve(entity, create=False)
        if start is None:
            return []
        results: dict[str, dict[str, Any]] = {}

        def walk(node: str, eff: float, chain: list[str], depth: int) -> None:
            owners = self.owners_of(node)
            if not owners or depth >= max_depth:
                if node != start:
                    r = results.setdefault(node, {"owner": node, "effective_pct": 0.0, "chains": []})
                    r["effective_pct"] += eff
                    r["chains"].append(chain)
                return
            for owner, pct, _src in owners:
                if owner in chain:  # circular ownership guard
                    continue
                walk(owner, eff * pct / 100.0, [*chain, owner], depth + 1)

        walk(start, 100.0, [start], 0)
        return sorted(
            (
                r | {"effective_pct": round(r["effective_pct"], 2)}
                for r in results.values()
                if r["effective_pct"] >= threshold
            ),
            key=lambda r: -r["effective_pct"],
        )

    def exposure(self, entity: str, flag: str, max_hops: int = 3) -> list[ExposurePath]:
        start = self.resolve(entity, create=False)
        if start is None:
            return []
        und = self.g.to_undirected(as_view=True)
        lengths = nx.single_source_shortest_path(und, start, cutoff=max_hops)
        out = []
        for node, path in lengths.items():
            if self.g.nodes[node].get(flag):
                reason = self.g.nodes[node].get("sanctions_list", flag)
                out.append(ExposurePath(node, len(path) - 1, path, str(reason)))
        return sorted(out, key=lambda e: e.hops)

    def offshore_layers(self, entity: str) -> int:
        start = self.resolve(entity, create=False)
        if start is None:
            return 0
        layers = 0
        seen = {start}
        frontier = [start]
        while frontier:
            nxt = []
            for n in frontier:
                for owner, _pct, _ in self.owners_of(n):
                    if owner in seen:
                        continue
                    seen.add(owner)
                    if self.g.nodes[owner].get("jurisdiction") in OFFSHORE:
                        layers += 1
                    nxt.append(owner)
            frontier = nxt
        return layers

    def neighbors_within(
        self, entity: str, hops: int = 2, kinds: tuple[str, ...] | None = None, relations: tuple[str, ...] | None = None
    ) -> list[str]:
        start = self.resolve(entity, create=False)
        if start is None:
            return []
        view = self.g
        if relations is not None:
            view = nx.subgraph_view(self.g, filter_edge=lambda u, v, k: self.g[u][v][k].get("relation") in relations)
        und = view.to_undirected(as_view=True)
        near = nx.single_source_shortest_path_length(und, start, cutoff=hops)
        return sorted(n for n in near if n != start and (kinds is None or self.g.nodes[n].get("kind") in kinds))

    def communities(self) -> list[set[str]]:
        und = nx.Graph()
        for u, v, d in self.g.edges(data=True):
            if d.get("relation") != "INCORPORATED_IN":
                und.add_edge(u, v)
        if und.number_of_edges() == 0:
            return []
        return [set(c) for c in nx.algorithms.community.greedy_modularity_communities(und)]

    def community_summary(self, members: set[str]) -> str:
        flagged = [m for m in members if self.g.nodes[m].get("sanctioned") or self.g.nodes[m].get("pep")]
        return f"Cluster of {len(members)} related parties: {', '.join(sorted(members))}." + (
            f" Flagged parties: {', '.join(sorted(flagged))}." if flagged else ""
        )

    def facts_for(self, entity: str, hops: int = 2) -> list[str]:
        """Linearised, cited subgraph facts for LLM context (local GraphRAG search)."""
        start = self.resolve(entity, create=False)
        if start is None:
            return []
        und = self.g.to_undirected(as_view=True)
        nodes = set(nx.single_source_shortest_path_length(und, start, cutoff=hops))
        facts = []
        for u, v, d in self.g.edges(data=True):
            if u in nodes and v in nodes:
                extra = f" ({d['pct']:.0f}%)" if "pct" in d else (f" as {d['role']}" if "role" in d else "")
                facts.append(f"{u} {d['relation']} {v}{extra} [{d.get('source', 'graph')}]")
        for n in sorted(nodes):
            data = self.g.nodes[n]
            if data.get("sanctioned"):
                src = data.get("sanctioned_source", "graph")
                facts.append(f"{n} is SANCTIONED ({data.get('sanctions_list', 'list')}) [{src}]")
            if data.get("pep"):
                facts.append(f"{n} is a politically exposed person [{data.get('pep_source', 'graph')}]")
        return sorted(set(facts))

    def subgraph_json(self, entity: str, hops: int = 2) -> dict[str, Any]:
        start = self.resolve(entity, create=False)
        if start is None:
            return {"nodes": [], "edges": []}
        und = self.g.to_undirected(as_view=True)
        nodes = set(nx.single_source_shortest_path_length(und, start, cutoff=hops))
        return {
            "nodes": [{"id": n, **{k: v for k, v in self.g.nodes[n].items() if k != "sources"}} for n in sorted(nodes)],
            "edges": [
                {"source": u, "target": v, **d} for u, v, d in self.g.edges(data=True) if u in nodes and v in nodes
            ],
        }

    def stats(self) -> dict[str, int]:
        return {"nodes": self.g.number_of_nodes(), "edges": self.g.number_of_edges()}
