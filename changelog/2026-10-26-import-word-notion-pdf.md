# Import Word files, Notion exports, CSV and PDFs

### Added
- Upload `.docx` (headings, lists and tables become Markdown), Notion export `.zip` files (clean names, working links, databases as tables, attachments left out, a zip inside a zip), other zips of documents, and `.csv` (a table). `POST /api/knowledge/collections/{id}/import` (an ordinary multipart file upload, so a file is not enlarged by an encoding); the Documents tab uses it for these types and keeps sending text files as text.
- PDF text import with `pypdf` 6.21 (BSD-3, pure Python, about 0.4 MB, now a normal dependency so the Docker image includes it). A PDF with no text layer is reported as scanned, a password-protected or damaged one is skipped with a reason, and only the first 400 pages are read. There is no OCR and none is planned.
- After an upload, a result panel lists what was imported and what was skipped and why.

### Security
- Limits on size and count, zip path-escape protection, refusal of XML entity declarations, and secret masking on imported text. A hostile or damaged file is skipped, never fatal.

### Changed
- **25 MB a file**, up to 20 files and 100 MB per upload (was an implied 12 MB). Raised consistently: the importer, a larger request cap for the import route only (`REPOLENS_MAX_UPLOAD_MB`, default 100; other requests keep `REPOLENS_MAX_BODY_MB`, 32), nginx in the web container (`client_max_body_size 101m`) and the Kubernetes ingress (`proxy-body-size: 101m`; its default of 1 MB would have refused imports).

### Fixed
- When the browser cannot reach the server, the app now says "Could not reach the server" (and, for a file upload, that the server may be restarting, out of date, or that the file is over a size limit) instead of the browser's "Failed to fetch".
