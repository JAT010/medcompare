import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, setAuthToken, setUnauthorizedHandler } from "../api/client";
import type { MeResponse, Role } from "../types/api";

interface AuthState {
  ready: boolean;
  user: MeResponse | null;
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  hasPermission: (perm: string) => boolean;
  isAdmin: boolean;
}

const AuthContext = createContext<AuthState | null>(null);

const TOKEN_KEY = "medcompare_token";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<MeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const clear = useCallback(() => {
    setAuthToken(null);
    sessionStorage.removeItem(TOKEN_KEY);
    setUser(null);
  }, []);

  const refresh = useCallback(async () => {
    const token = sessionStorage.getItem(TOKEN_KEY);
    if (!token) {
      setUser(null);
      setReady(true);
      return;
    }
    setAuthToken(token);
    try {
      const me = await api.me();
      setUser(me);
      setError(null);
    } catch {
      clear();
      setError(null);
    } finally {
      setReady(true);
    }
  }, [clear]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clear();
    });
    void refresh();
    return () => setUnauthorizedHandler(null);
  }, [refresh, clear]);

  const login = useCallback(async (username: string, password: string) => {
    const res = await api.login(username, password);
    // Prefer session storage over localStorage (shorter lifetime).
    sessionStorage.setItem(TOKEN_KEY, res.access_token);
    setAuthToken(res.access_token);
    const me = await api.me();
    setUser(me);
    setError(null);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      clear();
    }
  }, [clear]);

  const hasPermission = useCallback(
    (perm: string) => !!user && user.permissions.includes(perm as never),
    [user],
  );

  const value = useMemo<AuthState>(
    () => ({
      ready,
      user,
      error,
      login,
      logout,
      refresh,
      hasPermission,
      isAdmin: user?.role === ("admin" as Role),
    }),
    [ready, user, error, login, logout, refresh, hasPermission],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
