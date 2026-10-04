"""End-to-end demo: ingest the sample corpus, run three investigations, approve/reject via HITL.

    python scripts/demo.py            # offline (FakeLLM)
    SENTINEL_LLM_PROVIDER=anthropic SENTINEL_ANTHROPIC_API_KEY=... python scripts/demo.py
"""

from __future__ import annotations

from sentinel.agents.service import CaseService
from sentinel.container import Container


def main() -> None:
    c = Container.build()
    svc = CaseService(c)
    print(f"Indexed {c.store.count()} chunks | graph {c.graph.stats()} | model {c.risk.model_version}\n")
    for entity in ("Northwind Logistics Pvt Ltd", "Helios Trade FZE", "BlueOrbit Analytics Inc"):
        st = svc.start(entity, requested_by="alice")
        r = st["risk"]
        print(f"{entity:30s} risk={r['band']:6s} p={r['probability']:.2f} status={st['status']}")
        for reason in st["policy"]["reasons"]:
            print(f"    - {reason}")
        if st["awaiting_human"]:
            approve = r["band"] != "HIGH"
            done = svc.decide(st["approval_id"], "bob", approve, "demo decision")
            print(f"    reviewer bob -> {'APPROVED' if approve else 'REJECTED'} (final status: {done['status']})")
    print("\nAudit chain intact:", c.audit.verify()[0])
    print("\n" + svc.get(svc.list()[1]["case_id"])["report"][:1500])


if __name__ == "__main__":
    main()
