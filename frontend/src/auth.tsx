import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, AuthStatus, AuthUser, UNAUTHORIZED } from "./api";

interface AuthState {
  status: AuthStatus | null;
  user: AuthUser | null;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
  setUser: (u: AuthUser | null) => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await api.authStatus();
      setStatus(s);
      setUser(s.user);
      setFailed(null);
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const onUnauthorized = () => setUser(null);
    window.addEventListener(UNAUTHORIZED, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED, onUnauthorized);
  }, [refresh]);

  const signOut = useCallback(async () => {
    try { await api.logout(); } finally { setUser(null); refresh(); }
  }, [refresh]);

  if (failed && !status) {
    return <div className="empty">Can't reach the server: {failed}. <button className="btn btn-sm" onClick={refresh}>Retry</button></div>;
  }
  return <AuthContext.Provider value={{ status, user, refresh, signOut, setUser }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
