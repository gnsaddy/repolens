import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, NavLink, Outlet, Route, Routes, useLocation } from "react-router-dom";
import ErrorBoundary from "@/components/ErrorBoundary";
import { Activity, BarChart3, Library, FolderGit2, LayoutDashboard, Gauge, LogOut, Network, Users, GitFork, GitMerge, Search, Settings, ShieldAlert, Ticket } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./lib/api";
import { useAuth } from "./lib/auth";
import { cn } from "./lib/utils";
import { Spinner } from "./components/ui";
import CommandPalette from "./components/CommandPalette";
import Login, { SsoComplete } from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Repositories from "./pages/Repositories";
import Knowledge from "./pages/Knowledge";
import Risk from "./pages/Risk";
import SettingsPage from "./pages/SettingsPage";
import Investigations from "./pages/Investigations";
import InvestigationView from "./pages/InvestigationView";
const TokenUsage = lazy(() => import("./pages/TokenUsage"));
import Wiki from "./pages/Wiki";
import Ownership from "./pages/Ownership";
import RepoBrowser from "./pages/GitHubBrowse";
import JiraPage from "./pages/JiraPage";
import Profile from "./pages/Profile";
import Insights from "./pages/Insights";

const nav = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/investigations", label: "Investigations", icon: Search },
  { to: "/knowledge", label: "Knowledge", icon: Network },
  { to: "/wiki", label: "DeepMind Wiki", icon: Library },
  { to: "/risk", label: "Change Risk", icon: ShieldAlert },
  { to: "/ownership", label: "Ownership", icon: Users },
  { to: "/repositories", label: "Repositories", icon: FolderGit2 },
  { to: "/github", label: "GitHub", icon: GitFork },
  { to: "/bitbucket", label: "Bitbucket", icon: GitMerge },
  { to: "/jira", label: "Jira", icon: Ticket },
  { to: "/insights", label: "Insights", icon: Gauge },
  { to: "/usage", label: "Token Usage", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Settings },
];

function Shell() {
  const loc = useLocation();
  const { user, loading, logout } = useAuth();
  const [palette, setPalette] = useState(false);
  const drift = useQuery({ queryKey: ["drift-count"], enabled: !!user, queryFn: () => api<{ open: number }>("/wiki/drift/count"), refetchInterval: 60_000 });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((v) => !v); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  if (loading) return <div className="grid h-screen place-items-center"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col bg-sidebar p-3 text-slate-300 md:flex">
        <div className="mb-6 flex items-center gap-2 px-2 pt-2 text-white">
          <Activity className="h-5 w-5 text-indigo-400" /><span className="font-semibold">RepoLens AI</span>
        </div>
        <button onClick={() => setPalette(true)} className="mb-3 flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-left text-xs text-slate-400 hover:bg-white/10">
          <Search className="h-3.5 w-3.5" />Jump to…<kbd className="ml-auto rounded border border-white/20 px-1">Ctrl K</kbd>
        </button>
        <nav className="flex flex-1 flex-col gap-1" aria-label="Main">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end}
              className={({ isActive }) => cn("flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-white/10", isActive && "bg-brand text-white hover:bg-brand")}>
              <Icon className="h-4 w-4" />{label}
              {to === "/wiki" && !!drift.data?.open && <span title="Pages that may be out of date" className="ml-auto rounded-full bg-amber-400 px-1.5 text-xs font-medium text-slate-900">{drift.data.open}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-white/10 pt-3 text-xs">
          <NavLink to="/profile" className={({ isActive }) => cn("flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-white/10", isActive && "bg-white/10")} title="Your profile and sessions">
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-brand text-xs font-semibold text-white" aria-hidden>{(user.name || user.email)[0].toUpperCase()}</span>
            <span className="min-w-0"><span className="block truncate text-slate-200">{user.name || user.email}</span><span className="block truncate text-slate-400">{user.role}</span></span>
          </NavLink>
          <button onClick={logout} className="mt-2 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-slate-300 hover:bg-white/10">
            <LogOut className="h-4 w-4" />Sign out
          </button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 p-4 md:p-8">
        <nav className="mb-4 flex gap-2 overflow-x-auto md:hidden" aria-label="Mobile">
          {nav.map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => cn("whitespace-nowrap rounded-full border border-line px-3 py-1 text-xs", isActive && "bg-brand text-white")}>{label}</NavLink>
          ))}
        </nav>
        <ErrorBoundary resetKey={loc.pathname + loc.search}><Outlet /></ErrorBoundary>
      </main>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/sso-complete" element={<SsoComplete />} />
      <Route element={<Shell />}>
        <Route index element={<Dashboard />} />
        <Route path="investigations" element={<Investigations />} />
        <Route path="investigations/:id" element={<InvestigationView />} />
        <Route path="knowledge" element={<Knowledge />} />
        <Route path="wiki" element={<Wiki />} />
        <Route path="risk" element={<Risk />} />
        <Route path="ownership" element={<Ownership />} />
        <Route path="github" element={<RepoBrowser provider="github" />} />
        <Route path="bitbucket" element={<RepoBrowser provider="bitbucket" />} />
        <Route path="repositories" element={<Repositories />} />
        <Route path="jira" element={<JiraPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="profile" element={<Profile />} />
        <Route path="insights" element={<Insights />} />
        <Route path="usage" element={<Suspense fallback={<Spinner />}><TokenUsage /></Suspense>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
