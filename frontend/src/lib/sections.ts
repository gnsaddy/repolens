/** A long document is read in sections. A section is what starts at a level-2 heading (a PDF import has one "## Page N" per page). */
export interface Section { title: string; text: string }

/** Cut a document at its level-2 headings (a PDF import has one "## Page N" per page); level 1 if there are none. Fenced code is left whole. */
export function splitSections(markdown: string): Section[] {
  const lines = markdown.split("\n");
  for (const level of ["## ", "# "]) {
    const out: Section[] = [];
    const pre: string[] = [];
    let cur: Section | null = null, fence = false;
    for (const line of lines) {
      if (/^(```|~~~)/.test(line)) fence = !fence;
      if (!fence && line.startsWith(level) && !line.startsWith(`${level}#`)) {
        if (cur) out.push({ ...cur, text: cur.text.trimEnd() });
        else if (pre.some((l) => l.trim())) out.push({ title: pre.find((l) => /^# \S/.test(l))?.slice(2).trim() || "Start", text: pre.join("\n").trimEnd() });
        cur = { title: line.slice(level.length).trim() || "Untitled", text: line };
      } else if (cur) cur.text += `\n${line}`;
      else pre.push(line);
    }
    if (cur) out.push({ ...cur, text: cur.text.trimEnd() });
    if (out.length > 1) return out;
  }
  return [{ title: "Document", text: markdown }];
}


/** A page with more sections than this is shown one section at a time. */
export const PAGED_FROM = 12;
/** Showing every section of a very large page at once is slow; people are asked first. */
export const VERY_LARGE = 150;
