"""``sentinel`` command-line interface (operations & administration).

sentinel db upgrade
sentinel users create --username alice --roles analyst --display-name "Alice Analyst"   (password via prompt or
                                                                                          SENTINEL_NEW_PASSWORD)
sentinel users token --username mcp-svc --ttl-minutes 60
sentinel sources refresh --all | --source ofac_sdn
sentinel sources status
sentinel ml train --year 2024
sentinel ml drift --year 2025   (PSI of a newer SEC XBRL frame vs. the model reference; exit 2 = retrain)
sentinel investigate "Apple Inc." --as alice
sentinel audit verify
sentinel eval                    (offline release gates on recorded real data)
sentinel eval --screening-live   (screening recall/FPR against the full lists in the database)
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any

from sentinel.config import get_settings


def _container() -> Any:
    from sentinel.container import Container

    return Container.build()


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_db(args: argparse.Namespace) -> int:
    from sentinel.db.base import Database
    from sentinel.db.migrate import current_revision, head_revision

    db = Database(get_settings().database_url)
    if args.action == "upgrade":
        db.upgrade()
    _print({"current": current_revision(db.engine), "head": head_revision(db.engine)})
    return 0


def cmd_users(args: argparse.Namespace) -> int:
    from sentinel.security.users import UserError, UserService

    c = _container()
    users = UserService(c.db, c.settings.password_min_length)
    if args.action == "create":
        pw = os.environ.get("SENTINEL_NEW_PASSWORD") or getpass.getpass("Password: ")
        try:
            _print(users.create(args.username, pw, args.roles.split(","), args.tenant, args.display_name or ""))
        except UserError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        c.audit.append("cli", "user.created", "user", args.username, {"roles": args.roles.split(",")})
    elif args.action == "list":
        _print(users.list(args.tenant))
    elif args.action == "token":
        p = users.get_principal(args.username)
        if p is None:
            print("error: unknown or inactive user", file=sys.stderr)
            return 2
        from sentinel.security.auth import TokenService

        ts = TokenService(
            c.settings.jwt_secret.get_secret_value(), c.settings.jwt_algorithm, args.ttl_minutes, c.settings.jwt_issuer
        )
        token, exp = ts.issue(p.subject, list(p.roles), p.tenant, p.display_name)
        c.audit.append("cli", "token.issued", "user", p.subject, {"ttl_minutes": args.ttl_minutes})
        _print({"token": token, "expires_at": exp})
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    from sentinel.ingestion.reference import freshness

    c = _container()
    if args.action == "refresh":
        runs = c.reference.refresh_all("cli") if args.all else [c.reference.refresh(args.source, "cli")]
        _print([r.to_dict() | {"quality": {k: v for k, v in r.quality.items() if k != "documents"}} for r in runs])
        return 0 if all(r.status == "succeeded" for r in runs) else 1
    _print(freshness(c.db))
    return 0


def cmd_ml(args: argparse.Namespace) -> int:
    c = _container()
    if args.action == "drift":
        from sentinel.ml.drift import drift_report
        from sentinel.risk.peer_model import FEATURES, MODEL_NAME, build_dataset

        model, meta = c.registry.load(MODEL_NAME)
        current = build_dataset(c.fetcher, args.year)
        rep = drift_report(model.reference, current.X, FEATURES)
        _print(
            {
                "model_version": meta.get("version"),
                "reference_period": model.meta.get("period"),
                "current_period": current.period,
                "current_samples": len(current.X),
                "psi": rep.per_feature,
                "overall": rep.overall,
                "drifted": rep.drifted,
                "retrain_recommended": rep.retrain_recommended,
            }
        )
        return 2 if rep.retrain_recommended else 0
    from sentinel.ml.train import run

    res = run(c.fetcher, c.registry, args.year)
    _print(res.to_dict())
    return 0 if res.passed_gates else 1


def cmd_investigate(args: argparse.Namespace) -> int:
    from sentinel.agents.service import CaseService
    from sentinel.security.users import UserService

    c = _container()
    c.settings.runner_mode = "inline"
    p = UserService(c.db).get_principal(args.as_user)
    if p is None:
        print("error: unknown user", file=sys.stderr)
        return 2
    d = CaseService(c).create(p, args.query, cik=args.cik, lei=args.lei, question=args.question or "")
    _print(
        {
            k: d.get(k)
            for k in ("case_id", "status", "resolved_name", "risk_band", "risk_score", "policy_outcome", "error")
        }
        | {
            "policy_rules": [r["rule_id"] for r in (d.get("policy") or {}).get("rules", [])],
            "evidence": len(d.get("evidence") or []),
            "signals": len(d.get("signals") or []),
        }
    )
    return 0 if d.get("status") != "failed" else 1


def cmd_audit(_args: argparse.Namespace) -> int:
    res = _container().audit.verify()
    _print(res)
    return 0 if res["intact"] else 3


def cmd_eval(args: argparse.Namespace) -> int:
    if args.screening_live:
        # Screening recall/FPR against the *full* watch lists currently loaded in the configured database.
        import random

        from sentinel.eval.harness import GATES, eval_screening

        metrics, details = eval_screening(_container(), random.Random(args.seed))
        fails = [
            k
            for k, v in metrics.items()
            if (k.endswith("_max") and v > GATES[k]) or (not k.endswith("_max") and v < GATES[k])
        ]
        _print({"metrics": metrics, "gates": {k: GATES[k] for k in metrics}, "failures": fails} | details)
        return 1 if fails else 0
    from sentinel.eval.harness import run_offline_suite

    rep = run_offline_suite()
    _print(rep)
    return 0 if not rep["failures"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sentinel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("db")
    p.add_argument("action", choices=["upgrade", "current"])
    p.set_defaults(fn=cmd_db)
    p = sub.add_parser("users")
    p.add_argument("action", choices=["create", "list", "token"])
    p.add_argument("--username")
    p.add_argument("--roles", default="analyst")
    p.add_argument("--display-name")
    p.add_argument("--tenant", default="default")
    p.add_argument("--ttl-minutes", type=int, default=60)
    p.set_defaults(fn=cmd_users)
    p = sub.add_parser("sources")
    p.add_argument("action", choices=["refresh", "status"])
    p.add_argument("--source")
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_sources)
    p = sub.add_parser("ml")
    p.add_argument("action", choices=["train", "drift"])
    p.add_argument("--year", type=int, default=2024)
    p.set_defaults(fn=cmd_ml)
    p = sub.add_parser("investigate")
    p.add_argument("query")
    p.add_argument("--as", dest="as_user", required=True)
    p.add_argument("--cik", type=int)
    p.add_argument("--lei")
    p.add_argument("--question")
    p.set_defaults(fn=cmd_investigate)
    p = sub.add_parser("audit")
    p.add_argument("action", choices=["verify"])
    p.set_defaults(fn=cmd_audit)
    p = sub.add_parser("eval")
    p.add_argument("--screening-live", action="store_true", help="screening metrics against the loaded full lists")
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(fn=cmd_eval)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
