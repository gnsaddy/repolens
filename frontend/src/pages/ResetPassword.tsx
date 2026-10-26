import ThemeToggle from "@/components/ThemeToggle";
import { useMutation } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button, Card, ErrorNote, Input, Label } from "@/components/ui";
import { api } from "@/lib/api";

/** Open an invitation or reset link and choose a password. */
export default function ResetPassword() {
  const [sp] = useSearchParams();
  const token = sp.get("token") ?? "";
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const set = useMutation({ mutationFn: () => api("/auth/reset", { method: "POST", json: { token, password: pw } }) });
  const mismatch = !!again && pw !== again;
  const submit = (e: FormEvent) => { e.preventDefault(); if (!mismatch) set.mutate(); };
  return (
    <div className="relative grid min-h-screen place-items-center bg-canvas p-4">
      <ThemeToggle className="absolute right-3 top-3" />
      <Card className="w-full max-w-sm">
        <div className="mb-5 flex items-center gap-2"><Activity className="h-5 w-5 text-brand" /><span className="font-semibold">RepoLens AI</span></div>
        {set.isSuccess ? (<>
          <h1 className="mb-2 text-lg font-semibold">Password set</h1>
          <p className="mb-4 text-sm text-muted">You can sign in now. Any other device that was signed in has been signed out.</p>
          <Link to="/login" className="inline-flex h-9 items-center rounded-md bg-brand px-4 text-sm font-medium text-white hover:bg-brand-600">Go to sign in</Link>
        </>) : !token ? <p role="alert" className="text-sm text-red-600">This link is incomplete. Ask an admin for a new one.</p> : (<>
          <h1 className="mb-1 text-lg font-semibold">Choose a password</h1>
          <p className="mb-4 text-xs text-muted">At least 8 characters, not a common password, and not your e-mail name.</p>
          <form onSubmit={submit} className="space-y-3">
            <label className="block"><Label>New password</Label><Input type="password" autoFocus autoComplete="new-password" minLength={8} value={pw} onChange={(e) => setPw(e.target.value)} /></label>
            <label className="block"><Label>Repeat it</Label><Input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} /></label>
            {mismatch && <p role="alert" className="text-sm text-red-600">The passwords do not match.</p>}
            <ErrorNote error={set.error} />
            <Button className="w-full" disabled={pw.length < 8 || !again || mismatch || set.isPending}>{set.isPending ? "Saving…" : "Set password"}</Button>
          </form></>)}
      </Card>
    </div>
  );
}
