import { render, screen } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import { describe, expect, it } from "vitest";

// Everything a model or a document says reaches these screens as text. These checks make sure it can never become markup.
const sources = import.meta.glob("/src/**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

describe("untrusted text can never become markup", () => {
  it("no component injects raw HTML or enables raw HTML in Markdown", () => {
    const offenders = Object.entries(sources)
      .filter(([path]) => !path.includes("/test/"))
      .filter(([, code]) => /dangerouslySetInnerHTML|rehype-raw|rehypeRaw|\.innerHTML\s*=/.test(code))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });

  it("Markdown from a model shows hostile HTML as plain text and drops script links", () => {
    render(<ReactMarkdown>{'<img src=x onerror="alert(1)"> [click](javascript:alert(1)) <script>alert(2)</script>'}</ReactMarkdown>);
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
    const link = screen.getByText("click").closest("a");
    expect(link?.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
  });
});
