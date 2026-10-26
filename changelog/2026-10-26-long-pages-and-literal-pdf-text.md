# Long pages page by page, and no phantom links from PDFs

### Fixed
- A PDF full of code (a book with listings such as `tensor([[1, 14350, 385]])`) produced "5 links on this page point to pages that do not exist: 1, 14350, 385…" and hundreds of open wiki issues: its text was being read as Markdown, so `[[…]]` became wiki links, `[a](b)` became links and `# comment` lines became headings. PDF and Word text (and CSV cells) is now stored as literal text, and the link detectors ignore escaped brackets. **Re-upload an earlier import to fix it:** the same file name replaces the stored text.
- The settings and endpoints in "At a glance" now wrap instead of running off the card.

### Changed
- A wiki page with more than 12 sections (a PDF import has one per page) opens one section at a time with Previous, Next and a picker, instead of loading hundreds of pages together. "On this page" lists the sections and jumps between them; "Show all N sections" is there when you want everything, with a warning above 150 sections.

### Added
- A warning, not a refusal, for very large imports: a PDF over 100 pages, text over about 300,000 characters, a zip that adds more than 200 pages, or a document cut at the import limit (and, for a PDF over 400 pages, which pages were left out). It appears in the Import result with the reason (long documents crowd search results and the wiki health counts) and the rule of thumb (documents up to about 100 pages work best). The upload hint says the same before you upload.

### Fixed
- CI frontend lint: an unused assignment in the section splitter (`no-useless-assignment`, then `prefer-const`). Fixed, and `make check` now also runs the production build and the evaluation gate, so it covers what the CI backend and frontend jobs run (browser tests and the image scan still run only in CI).
