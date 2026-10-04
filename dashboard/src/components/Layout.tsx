import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import type { Review } from "../types";
import { useLoad } from "./ui";

const TITLES: Record<string, string> = {
  "": "Executive risk overview",
  investigations: "New investigation",
  cases: "Case explorer",
  reviews: "Human review queue",
  sources: "Data sources & lineage",
  audit: "Audit trail",
  governance: "Governance & model inventory",
};

export function Layout({ children }: { children: ReactNode }) {
  const { session, logout, can } = useAuth();
  const loc = useLocation();
  const canReview = can("review:decide");
  const { data: queue } = useLoad(() => (canReview ? api<Review[]>("/api/v1/reviews") : Promise.resolve([])), [canReview, loc.pathname], () => 30000);
  const section = loc.pathname.split("/")[1] ?? "";
  const actionable = (queue ?? []).filter((r) => r.can_decide).length;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="name">SENTINEL</div>
          <div className="tag">Third-party risk intelligence</div>
        </div>
        <nav className="nav" aria-label="Main">
          <div className="nav-section">Monitor</div>
          <NavLink to="/" end>Overview</NavLink>
          <NavLink to="/cases">Case explorer</NavLink>
          {can("case:create") && <NavLink to="/investigations/new">New investigation</NavLink>}
          {(canReview || can("case:read_all")) && (
            <NavLink to="/reviews">
              Review queue {actionable > 0 && <span className="count" data-testid="review-count">{actionable}</span>}
            </NavLink>
          )}
          <div className="nav-section">Govern</div>
          <NavLink to="/sources">Data sources</NavLink>
          {can("audit:read") && <NavLink to="/audit">Audit trail</NavLink>}
          <NavLink to="/governance">Governance</NavLink>
        </nav>
        <div className="user">
          <div className="who" data-testid="current-user">{session?.display_name || session?.username}</div>
          <div>{session?.roles.join(", ")} · {session?.tenant}</div>
          <button className="btn btn-ghost" onClick={() => void logout()}>Sign out</button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="crumbs">Sentinel / {TITLES[section] ?? section}</div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
