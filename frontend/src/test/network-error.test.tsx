import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/lib/api";

afterEach(() => vi.restoreAllMocks());

/** The error a request fails with (and a failure if it unexpectedly succeeds). */
const failure = async (request: Promise<unknown>) => (await request.then(() => { throw new Error("expected the request to fail"); }, (e: unknown) => e)) as ApiError;

describe("when the server cannot be reached", () => {
  it("says so in plain words instead of the browser's 'Failed to fetch'", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await failure(api("/repositories"));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Could not reach the server. Check that it is running and try again.");
    expect(err.message).not.toContain("Failed to fetch");
  });

  it("points at the size limit when the failed request was a file upload", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const form = new FormData();
    form.append("files", new File(["x"], "report.pdf"));
    const err = await failure(api("/knowledge/collections/c1/import", { method: "POST", body: form }));
    expect(err.message).toMatch(/while uploading/);
    expect(err.message).toMatch(/size limit/);
  });

  it("still reports a normal server answer as before", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: "Nope" }), { status: 403, headers: { "Content-Type": "application/json" } }));
    const err = await failure(api("/x"));
    expect(err.status).toBe(403);
    expect(err.message).toBe("Nope");
  });
});
