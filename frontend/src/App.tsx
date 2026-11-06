import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, NavLink, Outlet, Route, Routes, useLocation } from "react-router-dom";
import Logo from "@/components/Logo";
import ErrorBoundary from "@/components/ErrorBoundary";
import { useTheme } from "@/lib/theme";
import { BookOpen, BrainCircuit, Bug, Bot, Coins, FolderGit2, Gauge, GitFork, GitPullRequestArrow, Hammer, LayoutDashboard, LogOut, MessageSquareText, Moon, Network, ScrollText, Search, Settings, ShieldAlert, Sun, Ticket, Users, type LucideIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./lib/api";
import { useAuth } from "./lib/auth";
import { cn } from "./lib/utils";
import { APP_VERSION } from "./lib/version";
import { Spinner } from "./components/ui";
import CommandPalette from "./components/CommandPalette";
import ReportIssue from "./components/ReportIssue";
import AskFab from "./components/AskFab";
import Login, { SsoComplete } from "./pages/Login";
import About from "./pages/About";
import Security from "./pages/Security";
import Faq from "./pages/Faq";
import Dashboard from "./pages/Dashboard";
const Repositories = lazy(() => import("./pages/Repositories"));
const Knowledge = lazy(() => import("./pages/Knowledge"));
const GraphPage = lazy(() => import("./pages/Graph"));
const FeaturesPage = lazy(() => import("./pages/Features"));
const PersonasPage = lazy(() => import("./pages/Personas"));
const Risk = lazy(() => import("./pages/Risk"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const Investigations = lazy(() => import("./pages/Investigations"));
const InvestigationView = lazy(() => import("./pages/InvestigationView"));
const Ask = lazy(() => import("./pages/Ask"));
const TokenUsage = lazy(() => import("./pages/TokenUsage"));
const Wiki = lazy(() => import("./pages/Wiki"));
const Ownership = lazy(() => import("./pages/Ownership"));
const RepoBrowser = lazy(() => import("./pages/GitHubBrowse"));
const JiraPage = lazy(() => import("./pages/JiraPage"));
const Profile = lazy(() => import("./pages/Profile"));
const Insights = lazy(() => import("./pages/Insights"));
const AuditLog = lazy(() => import("./pages/AuditLog"));
import ResetPassword from "./pages/ResetPassword";

type NavItem = { to: string; label: string; icon: LucideIcon; end?: boolean; adminOnly?: boolean };
/** The sidebar, in groups of what people do together: the work itself, the knowledge it builds on, where the code comes
 *  from, and how it is going. */
const groups: { label: string; items: NavItem[] }[] = [
  { label: "", items: [{ to: "/", label: "Dashboard", icon: LayoutDashboard, end: true }] },
  { label: "Work", items: [
    { to: "/ask", label: "Ask", icon: MessageSquareText },
    { to: "/investigations", label: "Investigations", icon: Bug },
    { to: "/features", label: "Build a feature", icon: Hammer },
    { to: "/personas", label: "Agent personas", icon: Bot },
    { to: "/risk", label: "Change Risk", icon: ShieldAlert },
  ] },
  { label: "Knowledge", items: [
    { to: "/knowledge", label: "Knowledge", icon: BrainCircuit },
    { to: "/wiki", label: "DeepMind Wiki", icon: BookOpen },
    { to: "/graph", label: "Graph", icon: Network },
    { to: "/ownership", label: "Ownership", icon: Users },
  ] },
  { label: "Sources", items: [
    { to: "/repositories", label: "Repositories", icon: FolderGit2 },
    { to: "/github", label: "GitHub", icon: GitFork },
    { to: "/bitbucket", label: "Bitbucket", icon: GitPullRequestArrow },
    { to: "/jira", label: "Jira", icon: Ticket },
  ] },
  { label: "Monitor", items: [
    { to: "/insights", label: "Insights", icon: Gauge },
    { to: "/usage", label: "Token Usage", icon: Coins },
    { to: "/audit", label: "Audit log", icon: ScrollText, adminOnly: true },
    { to: "/settings", label: "Settings", icon: Settings },
  ] },
];
const nav = groups.flatMap((g) => g.items);

function Shell() {
  const loc = useLocation();
  const theme = useTheme();
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
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col overflow-y-auto border-r border-line bg-surface p-3 md:flex">
        <div className="mb-4 flex items-center gap-2 px-2 pt-2">
          <Logo className="h-7 w-7" /><span className="font-semibold tracking-tight">RepoLens AI</span>
        </div>
        <button onClick={() => setPalette(true)} className="mb-4 flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-left caption-5 text-muted hover:bg-slate-50">
          <Search className="h-3.5 w-3.5" />Jump to…<kbd className="ml-auto rounded-lg border border-line px-1 text-slate-400">Ctrl K</kbd>
        </button>
        <nav className="flex flex-1 flex-col" aria-label="Main">
          {groups.map((g) => {
            const items = g.items.filter((n) => !n.adminOnly || user.role === "admin");
            return (
              <div key={g.label || "top"} className={g.label ? "mt-4" : ""}>
                {g.label && <div className="px-3 pb-1 text-[11px] font-medium uppercase tracking-wider text-slate-400">{g.label}</div>}
                <div className="flex flex-col gap-0.5">
                  {items.map(({ to, label, icon: Icon, end }) => (
                    <NavLink key={to} to={to} end={end}
                      className={({ isActive }) => cn("group flex items-center gap-3 rounded-lg px-3 py-2 body-6 text-slate-600 hover:bg-slate-100 hover:text-ink", isActive && "bg-indigo-50 font-medium text-brand hover:bg-indigo-50 hover:text-brand")}>
                      {({ isActive }) => (<>
                        <Icon className={cn("h-4 w-4 shrink-0", isActive ? "text-brand" : "text-slate-400 group-hover:text-slate-600")} aria-hidden />{label}
                        {to === "/wiki" && !!drift.data?.open && <span title="Pages that may be out of date" className="ml-auto rounded-full bg-amber-100 px-2 caption-4 text-amber-700">{drift.data.open}</span>}
                      </>)}
                    </NavLink>
                  ))}
                </div>
              </div>
            );
          })}
        </nav>
        <div className="mt-4 border-t border-line pt-3 caption-5">
          <NavLink to="/profile" className={({ isActive }) => cn("flex items-center gap-2 rounded-lg px-2 py-2 hover:bg-slate-100", isActive && "bg-slate-100")} title="Your profile and sessions">
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-brand caption-3 text-white" aria-hidden>{(user.name || user.email)[0].toUpperCase()}</span>
            <span className="min-w-0"><span className="block truncate text-ink">{user.name || user.email}</span><span className="block truncate text-muted">{user.role}</span></span>
          </NavLink>
          <button onClick={theme.toggle} aria-label={theme.dark ? "Switch to day mode" : "Switch to night mode"} className="mt-2 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-slate-600 hover:bg-slate-100">
            {theme.dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}{theme.dark ? "Day mode" : "Night mode"}
          </button>
          <button onClick={logout} className="mt-1 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-slate-600 hover:bg-slate-100">
            <LogOut className="h-4 w-4" />Sign out
          </button>
          <ReportIssue />
          <div className="mt-3 px-2 text-[11px] text-slate-400" title="Beta: expect rough edges and changes">RepoLens AI <span className="font-medium text-slate-500">{APP_VERSION}</span> <span aria-hidden>·</span> <NavLink className="hover:text-ink hover:underline" to="/about">About</NavLink> <span aria-hidden>·</span> <NavLink className="hover:text-ink hover:underline" to="/security">Security</NavLink> <span aria-hidden>·</span> <NavLink className="hover:text-ink hover:underline" to="/faq">FAQ</NavLink></div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 p-4 md:p-8">
        <nav className="mb-4 flex gap-2 overflow-x-auto md:hidden" aria-label="Mobile">
          {nav.filter((n) => !n.adminOnly || user.role === "admin").map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => cn("whitespace-nowrap rounded-full border border-line px-3 py-1 caption-5", isActive && "bg-brand text-white")}>{label}</NavLink>
          ))}
        </nav>
        <ErrorBoundary resetKey={loc.pathname + loc.search}><Suspense fallback={<div className="py-10"><Spinner /></div>}><Outlet /></Suspense></ErrorBoundary>
      </main>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <AskFab />
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/sso-complete" element={<SsoComplete />} />
      <Route path="/reset" element={<ResetPassword />} />
      <Route path="/about" element={<About />} />
      <Route path="/security" element={<Security />} />
      <Route path="/faq" element={<Faq />} />
      <Route element={<Shell />}>
        <Route index element={<Dashboard />} />
        <Route path="ask" element={<Ask />} />
        <Route path="ask/:id" element={<Ask />} />
        <Route path="investigations" element={<Investigations />} />
        <Route path="investigations/:id" element={<InvestigationView />} />
        <Route path="knowledge" element={<Knowledge />} />
        <Route path="graph" element={<GraphPage />} />
        <Route path="features" element={<FeaturesPage />} />
        <Route path="personas" element={<PersonasPage />} />
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
        <Route path="audit" element={<AuditLog />} />
        <Route path="people" element={<Navigate to="/settings?tab=people" replace />} />
        <Route path="usage" element={<Suspense fallback={<Spinner />}><TokenUsage /></Suspense>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
