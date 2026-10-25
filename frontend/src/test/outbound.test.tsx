import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import OutboundCard from "@/components/OutboundCard";

const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } }));
afterEach(() => vi.restoreAllMocks());
const local = ["Search and the wiki run on this server only."];
const show = (payload: unknown) => {
  vi.spyOn(globalThis, "fetch").mockImplementation(() => json(payload));
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><OutboundCard /></MemoryRouter></QueryClientProvider>);
};

describe("What leaves this server", () => {
  it("says so plainly when nothing is connected", async () => {
    show({ items: [], external: 0, summary: "Nothing leaves this server.", local_only: local });
    expect(await screen.findByText("Nothing leaves")).toBeInTheDocument();
    expect(screen.getByText(/No model, embedding service or connector is connected/)).toBeInTheDocument();
  });
  it("lists each destination with what it receives and where to turn it off", async () => {
    show({ external: 1, summary: "1 connection sends data off this server.", local_only: local, items: [
      { id: "llm:1", name: "Language model: gemini", kind: "model", host: "generativelanguage.googleapis.com", stays_on_machine: false, sends: ["your questions"], where: "Settings → AI & search → Models and routing", note: "Models in use: flash" },
      { id: "llm:2", name: "Language model: openai", kind: "model", host: "localhost:11434", stays_on_machine: true, sends: ["your questions"], where: "Settings → AI & search", note: "" }] });
    const list = await screen.findByRole("list", { name: "Outbound connections" });
    expect(screen.getByText("1 outbound")).toBeInTheDocument();
    expect(within(list).getByText("leaves this server")).toBeInTheDocument();
    expect(within(list).getByText("stays on this machine")).toBeInTheDocument();
    expect(within(list).getByText(/Manage: Settings → AI & search → Models and routing/)).toBeInTheDocument();
  });
});
