import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import ReportIssue from "@/components/ReportIssue";

describe("Report a bug / request a feature", () => {
  it("opens the public issue forms in a new tab and warns that issues are public", async () => {
    render(<ReportIssue />);
    await userEvent.click(screen.getByRole("button", { name: /report a bug/i }));
    const dlg = screen.getByRole("dialog");
    const bug = within(dlg).getByRole("link", { name: /report a bug/i });
    expect(bug).toHaveAttribute("href", expect.stringContaining("/issues/new?template=bug_report.yml"));
    expect(bug).toHaveAttribute("target", "_blank");
    expect(bug).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(within(dlg).getByRole("link", { name: /request a feature/i })).toHaveAttribute("href", expect.stringContaining("template=feature_request.yml"));
    expect(within(dlg).getByText(/issues on github are public/i)).toBeInTheDocument();
    expect(within(dlg).getByRole("link", { name: "SECURITY.md" })).toHaveAttribute("href", expect.stringContaining("SECURITY.md"));
  });
});
