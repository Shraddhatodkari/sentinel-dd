"""Case knowledge graph (GraphRAG) built from persisted, evidence-backed relationships.

Nodes are the subject, its GLEIF consolidation parents/children, Exhibit-21 subsidiaries and Form 3/4 insiders.
Screening hits flag nodes. Analytics produce *explainable paths* — every edge carries the evidence id that
supports it — and linearised, cited facts that ground the narrative.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

import networkx as nx

from sentinel.rag.case_index import CITATION_PREFIX_LEN


@dataclass
class RelRow:
    subject: str
    subject_type: str
    relation: str
    object: str
    object_type: str
    evidence_id: str
    attributes: dict[str, Any]


def build_graph(subject: str, rels: list[RelRow], flags: dict[str, dict[str, Any]]) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_node(subject, kind="company", subject=True)
    for r in rels:
        for n, t in ((r.subject, r.subject_type), (r.object, r.object_type)):
            if n not in g:
                g.add_node(n, kind=t, subject=n == subject)
        g.add_edge(
            r.subject,
            r.object,
            relation=r.relation,
            evidence_id=r.evidence_id,
            basis=r.attributes.get("basis", ""),
            **{k: v for k, v in r.attributes.items() if k != "basis"},
        )
    for name, f in flags.items():
        if name in g:
            g.nodes[name].update(f)
        else:
            g.add_node(name, kind=f.get("kind", "unknown"), **f)
    return g


def analyse(g: nx.DiGraph, subject: str, max_hops: int = 3) -> dict[str, Any]:
    und = g.to_undirected(as_view=True)
    dist = nx.single_source_shortest_path(und, subject, cutoff=max_hops) if subject in g else {}
    exposure = []
    for node, path in dist.items():
        data = g.nodes[node]
        if node != subject and (data.get("sanctioned") or data.get("pep")):
            edges = []
            for a, b in itertools.pairwise(path):
                e = g.get_edge_data(a, b) or g.get_edge_data(b, a) or {}
                edges.append({"from": a, "to": b, "relation": e.get("relation"), "evidence_id": e.get("evidence_id")})
            exposure.append(
                {
                    "target": node,
                    "hops": len(path) - 1,
                    "path": path,
                    "edges": edges,
                    "flag": "sanctions" if data.get("sanctioned") else "pep",
                    "evidence_ids": data.get("evidence_ids", []),
                }
            )
    parents = [u for u, _, d in g.in_edges(subject, data=True) if str(d.get("relation", "")).startswith("CONSOLIDATES")]
    ultimate = next(
        (u for u, _, d in g.in_edges(subject, data=True) if d.get("relation") == "CONSOLIDATES_ULTIMATE"), None
    )
    counts: dict[str, int] = {}
    for _, _, d in g.edges(data=True):
        counts[d["relation"]] = counts.get(d["relation"], 0) + 1
    return {
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "relation_counts": counts,
        "consolidation_parents": parents,
        "ultimate_parent": ultimate,
        "insiders": sorted(u for u, _, d in g.in_edges(subject, data=True) if d.get("relation") == "INSIDER_OF"),
        "exposure_paths": sorted(exposure, key=lambda x: x["hops"]),
        "subject_flags": {k: g.nodes[subject].get(k) for k in ("sanctioned", "pep") if g.nodes[subject].get(k)}
        if subject in g
        else {},
    }


def facts(g: nx.DiGraph, limit: int = 60) -> list[str]:
    out = []
    for u, v, d in g.edges(data=True):
        cite = (d.get("evidence_id") or "")[:CITATION_PREFIX_LEN]
        out.append(f"{u} {d['relation']} {v}" + (f" ({d['basis']})" if d.get("basis") else "") + f" [{cite}#0]")
    return out[:limit]


def to_json(g: nx.DiGraph, subject: str, max_nodes: int = 120) -> dict[str, Any]:
    keep = [subject] if subject in g else []
    keep += [n for n in g.nodes if n != subject and (g.nodes[n].get("sanctioned") or g.nodes[n].get("pep"))]
    keep += [n for n in g.nodes if n not in keep]
    keep_set = set(keep[:max_nodes])
    return {
        "nodes": [
            {
                "id": n,
                **{k: v for k, v in g.nodes[n].items() if k != "evidence_ids"},
                "evidence_ids": g.nodes[n].get("evidence_ids", []),
            }
            for n in keep
            if n in keep_set
        ],
        "edges": [{"source": u, "target": v, **d} for u, v, d in g.edges(data=True) if u in keep_set and v in keep_set],
        "truncated": g.number_of_nodes() > max_nodes,
    }
