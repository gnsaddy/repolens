import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler, tokenStore, type User } from "./api";

interface AuthCtx { user: User | null; loading: boolean; login: (e: string, p: string) => Promise<void>; completeSso: (code: string) => Promise<void>;
  register: (e: string, p: string, org: string, name: string) => Promise<void>; logout: () => void }
const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(!!tokenStore.get());

  const logout = useCallback(() => { tokenStore.set(null); setUser(null); }, []);
  useEffect(() => { setUnauthorizedHandler(logout); }, [logout]);
  useEffect(() => {
    if (!tokenStore.get()) return;
    api<User>("/auth/me").then(setUser).catch(() => tokenStore.set(null)).finally(() => setLoading(false));
  }, []);

  const accept = (r: { access_token: string; user: User }) => { tokenStore.set(r.access_token); setUser(r.user); };
  const completeSso = async (code: string) => accept(await api("/auth/sso/exchange", { method: "POST", json: { code } }));
  const login = async (email: string, password: string) =>
    accept(await api("/auth/login", { method: "POST", json: { email, password } }));
  const register = async (email: string, password: string, organization: string, name: string) =>
    accept(await api("/auth/register", { method: "POST", json: { email, password, organization, name } }));

  return <Ctx.Provider value={{ user, loading, login, completeSso, register, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}

export const canWrite = (u: User | null) => u?.role === "admin" || u?.role === "member";
