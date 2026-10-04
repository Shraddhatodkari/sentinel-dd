import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, loadSession, saveSession, setUnauthorizedHandler } from "./api";
import type { Session } from "./types";

interface AuthCtx {
  session: Session | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (perm: string) => boolean;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  useEffect(() => setUnauthorizedHandler(() => setSession(null)), []);
  useEffect(() => {
    if (!session) return;
    const ms = session.expires_at * 1000 - Date.now();
    const t = window.setTimeout(() => {
      saveSession(null);
      setSession(null);
    }, Math.max(ms, 0));
    return () => window.clearTimeout(t);
  }, [session]);
  const value: AuthCtx = {
    session,
    async login(username, password) {
      const s = await api<Session>("/api/v1/auth/login", { method: "POST", json: { username, password } });
      saveSession(s);
      setSession(s);
    },
    async logout() {
      try {
        await api("/api/v1/auth/logout", { method: "POST" });
      } finally {
        saveSession(null);
        setSession(null);
      }
    },
    can: (perm) => !!session?.permissions.includes(perm),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("AuthProvider missing");
  return c;
}
