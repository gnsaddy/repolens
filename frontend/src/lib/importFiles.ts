/** Files people bring from their laptop. Plain text goes up as text; Word, PDF, CSV and zip files are sent as ordinary file uploads and read on the server. */
export const TEXT_ACCEPT = ".md,.mdx,.rst,.txt,.adoc";
export const IMPORT_ACCEPT = `${TEXT_ACCEPT},.docx,.pdf,.zip,.csv`;
export const MAX_TEXT_BYTES = 1_000_000;
export const MAX_IMPORT_BYTES = 25 * 1024 * 1024;
export const MAX_IMPORT_TOTAL = 100 * 1024 * 1024;

export const needsImport = (name: string) => /\.(docx|pdf|zip|csv)$/i.test(name);

export interface ImportResult {
  received: number; changed: number; secrets_redacted: number;
  imported: { path: string; source: string; kind: string }[];
  skipped: { name: string; reason: string }[];
  /** very large documents: imported, but worth knowing about */
  warnings?: string[];
}

/** One sentence for the person: what was imported and what was not. */
export function summarize(r: ImportResult): string {
  const n = r.imported.length;
  const parts = [n ? `Imported ${n} document${n === 1 ? "" : "s"}` : "Nothing was imported"];
  if (r.skipped.length) parts.push(`skipped ${r.skipped.length}`);
  if (r.warnings?.length) parts.push(`${r.warnings.length} warning${r.warnings.length === 1 ? "" : "s"}`);
  if (r.secrets_redacted) parts.push(`${r.secrets_redacted} secret${r.secrets_redacted === 1 ? "" : "s"} masked`);
  return `${parts.join(", ")}.`;
}
