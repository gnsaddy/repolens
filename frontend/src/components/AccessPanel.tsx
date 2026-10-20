import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, CardTitle, ErrorNote } from "@/components/ui";
import { api, type AccessInfo, type User } from "@/lib/api";

/** Admin-only: restrict a repository so only listed users (and admins) can see it or anything derived from it. */
export default function AccessPanel({ repoId, repoName }: { repoId: string; repoName: string }) {
  const qc = useQueryClient();
  const access = useQuery({ queryKey: ["repo-access", repoId], queryFn: () => api<AccessInfo>(`/repositories/${repoId}/access`) });
  const users = useQuery({ queryKey: ["org-users"], queryFn: () => api<User[]>("/auth/users") });
  const [visibility, setVisibility] = useState<"org" | "restricted">("org");
  const [selected, setSelected] = useState<string[]>([]);
  useEffect(() => {
    if (access.data) { setVisibility(access.data.visibility); setSelected(access.data.users.map((u) => u.id)); }
  }, [access.data]);
  const save = useMutation({
    mutationFn: () => api<AccessInfo>(`/repositories/${repoId}/access`, { method: "PUT", json: { visibility, user_ids: selected } }),
    onSuccess: (d) => { qc.setQueryData(["repo-access", repoId], d); qc.invalidateQueries({ queryKey: ["repos"] }); },
  });
  const members = (users.data ?? []).filter((u) => u.role !== "admin"); // admins always have access
  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  return (
    <Card>
      <CardTitle right={access.data?.visibility === "restricted" && <Badge tone="amber"><Lock className="mr-1 h-3 w-3" />restricted</Badge>}>Access</CardTitle>
      <div role="radiogroup" aria-label={`Visibility of ${repoName}`} className="space-y-2 text-sm">
        <label className="flex items-start gap-2"><input type="radio" name="vis" checked={visibility === "org"} onChange={() => setVisibility("org")} />
          <span><b>Everyone in the organisation</b><span className="block text-xs text-muted">Default. Roles (admin / member / viewer) still apply.</span></span></label>
        <label className="flex items-start gap-2"><input type="radio" name="vis" checked={visibility === "restricted"} onChange={() => setVisibility("restricted")} />
          <span><b>Restricted</b><span className="block text-xs text-muted">Hidden — including its code, history, graph nodes, search results and investigations — from everyone not listed below. Admins always have access.</span></span></label>
      </div>
      {visibility === "restricted" && (
        <fieldset className="mt-3 space-y-1.5 border-t border-line pt-3">
          <legend className="mb-1 text-xs font-medium text-muted">Users with access</legend>
          {!members.length && <p className="text-xs text-muted">No non-admin users yet.</p>}
          {members.map((u) => (
            <label key={u.id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selected.includes(u.id)} onChange={() => toggle(u.id)} />
              {u.email}<Badge>{u.role}</Badge></label>))}
        </fieldset>)}
      <ErrorNote error={save.error ?? access.error} />
      <div className="mt-3"><Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>Save access</Button>
        {save.isSuccess && <span role="status" className="ml-3 text-xs text-emerald-700">Saved</span>}</div>
    </Card>
  );
}
