import { type ReactNode } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";

export const slug = (text: string) => text.toLowerCase().replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-");
const textOf = (node: ReactNode): string => (typeof node === "string" ? node : Array.isArray(node) ? node.map(textOf).join("") : node && typeof node === "object" && "props" in node ? textOf((node as { props: { children?: ReactNode } }).props.children) : "");

/** Wiki Markdown. wiki://repo/path#anchor links navigate inside the app; everything else opens safely in a new tab. */
export default function Markdown({ text, onOpen }: { text: string; onOpen: (repoId: string, path: string, anchor?: string) => void }) {
  const heading = (Tag: "h1" | "h2" | "h3" | "h4") => ({ children }: { children?: ReactNode }) => <Tag id={slug(textOf(children))}>{children}</Tag>;
  return (
    <div className="prose-wiki">
      <ReactMarkdown
        urlTransform={(url) => (url.startsWith("wiki://") ? url : defaultUrlTransform(url))}
        components={{
          h1: heading("h1"), h2: heading("h2"), h3: heading("h3"), h4: heading("h4"),
          a: ({ href, children }) => {
            if (href?.startsWith("wiki://")) {
              const [, rest] = href.split("wiki://");
              const [target, anchor] = rest.split("#");
              const i = target.indexOf("/");
              return <a href={`#${target}`} onClick={(e) => { e.preventDefault(); onOpen(target.slice(0, i), target.slice(i + 1), anchor); }}>{children}</a>;
            }
            return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
          },
        }}
      >{text}</ReactMarkdown>
    </div>
  );
}
