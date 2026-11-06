import { CaretDown } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import PublicLayout from "@/components/PublicLayout";
import { FAQ } from "@/lib/trust";

export default function Faq() {
  return (
    <PublicLayout title="Questions about trust" intro="What people ask before they install it, answered plainly. If something here is out of date or unclear, tell us and we will fix it.">
      <section aria-label="Frequently asked questions">
        <ul className="space-y-2">
          {FAQ.map((f, i) => (
            <li key={f.id} id={f.id} className="scroll-mt-6 rounded-lg border border-l-line">
              <details open={i === 0} className="group p-4">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 font-medium"><span>{f.q}</span><CaretDown size={20} aria-hidden className="shrink-0 text-l-muted transition-transform duration-500 ease-land group-open:rotate-180" /></summary>
                <p className="mt-2 body-6 text-l-muted">{f.a}</p>
              </details>
            </li>))}
        </ul>
        <p className="mt-6 body-6 text-l-muted">More detail: <Link className="text-l-link hover:underline" to="/security">the Security page</Link>, with where each control stands, and <Link className="text-l-link hover:underline" to="/about">About</Link>.</p>
      </section>
    </PublicLayout>
  );
}
