import io
import sys
import zipfile

import pytest

from repolens.services import importers
from tests.conftest import register
from tests.test_knowledge import make_collection

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(body_xml: str, header: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"{header}<w:document {NS}><w:body>{body_xml}</w:body></w:document>")
    return buf.getvalue()


def para(text: str, style: str | None = None, list_level: int | None = None) -> str:
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if list_level is not None:
        ppr += f'<w:numPr><w:ilvl w:val="{list_level}"/><w:numId w:val="1"/></w:numPr>'
    return f"<w:p><w:pPr>{ppr}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"


def table(rows: list[list[str]]) -> str:
    return (
        "<w:tbl>"
        + "".join("<w:tr>" + "".join(f"<w:tc>{para(c)}</w:tc>" for c in r) + "</w:tr>" for r in rows)
        + "</w:tbl>"
    )


def zipped(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


ID_A, ID_B = "1a2b3c4d5e6f7890abcdef1234567890", "0f9e8d7c6b5a43210fedcba987654321"


# ---------------------------------------------------------------- Word
def test_word_becomes_markdown_with_headings_lists_and_tables():
    data = docx(
        para("Runbook", "Title")
        + para("Restart", "Heading1")
        + para("Check the pod")
        + para("first", list_level=0)
        + para("nested", list_level=1)
        + table([["Step", "Owner"], ["Drain", "ops|team"]])
        + para("Details", "Heading 2")
    )
    [doc], skips = importers.convert("My Runbook.docx", data)
    assert skips == [] and doc.path == "My Runbook.md" and doc.kind == "docx"
    text = doc.content
    assert (
        "> Imported from My Runbook.docx" in text
        and "# Runbook" in text
        and "# Restart" in text
        and "## Details" in text
    )
    assert "- first" in text and "  - nested" in text
    assert "| Step | Owner |" in text and "| Drain | ops\\|team |" in text  # a pipe in a cell cannot break the table


@pytest.mark.parametrize(
    "name,data,reason",
    [
        ("notes.docx", b"this is not a zip", "not a Word"),
        ("empty.docx", docx(""), "no text"),
        ("bomb.docx", docx(para("x"), '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa">]>'), "not allowed"),
        ("ent.docx", docx(para("&a;"), '<!ENTITY a "boom">'), "not allowed"),
        ("bad.docx", docx("<w:p><unclosed>"), "not valid"),
        ("big.md", b"x" * (importers.MAX_FILE + 1), "larger than"),
        ("image.png", b"\x89PNG", "not supported"),
        ("binary.txt", b"abc\x00def", "binary"),
    ],
)
def test_files_that_cannot_be_read_are_skipped_with_a_reason(name, data, reason):
    docs, skips = importers.convert(name, data)
    assert docs == [] and len(skips) == 1 and reason in skips[0].reason and skips[0].name == name


def test_plain_text_and_csv_pass_through_or_become_tables():
    [doc], _ = importers.convert("README.md", b"# Hello\n\nworld")
    assert (doc.path, doc.content, doc.kind) == ("README.md", "# Hello\n\nworld", "text")
    [t], _ = importers.convert("People.csv", b"Name,Role\nAda,Engineer\n")
    assert t.path == "People.md" and "| Name | Role |" in t.content and "| Ada | Engineer |" in t.content


# ---------------------------------------------------------------- PDF
def test_pdf_without_the_optional_library_says_so_instead_of_failing(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)  # makes `import pypdf` fail
    docs, skips = importers.convert("spec.pdf", b"%PDF-1.4 anything")
    assert docs == [] and "not available on this server" in skips[0].reason


def make_pdf(pages: list[str], pad: int = 0) -> bytes:
    """A small real PDF with one line of selectable text per page (an empty string makes a page with no text)."""
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for text in pages:
        content_id, page_id = 4 + 2 * len(kids) + 1, 4 + 2 * len(kids)
        kids.append(page_id)
        lit = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 14 Tf 40 700 Td ({lit}) Tj ET".encode() if text else b""
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    if (
        pad
    ):  # an unreferenced object of `pad` bytes, like the images a real report carries: makes the file big, not the text
        objs.append(b"<< /Length %d >>\nstream\n" % pad + b"\x00" * pad + b"\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(
        f"{off:010d} 00000 n \n".encode() for off in offsets
    )
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def test_a_pdf_with_text_becomes_a_page_per_section():
    [doc], skips = importers.convert(
        "Quarterly Report.pdf", make_pdf(["Revenue grew twelve percent", "Costs stayed flat this quarter"])
    )
    assert skips == [] and doc.path == "Quarterly Report.md" and doc.kind == "pdf"
    assert "> Imported from Quarterly Report.pdf" in doc.content and "# Quarterly Report" in doc.content
    assert "## Page 1" in doc.content and "Revenue grew twelve percent" in doc.content
    assert "## Page 2" in doc.content and "Costs stayed flat this quarter" in doc.content


def test_a_large_pdf_with_a_little_text_is_read_in_full():
    big = make_pdf(["Only this sentence is text on the first page", "and this is the second page"], pad=24 * MB)
    assert 24 * MB < len(big) <= importers.MAX_FILE  # inside the 25 MB limit
    [doc], skips = importers.convert("Annual Report.pdf", big)
    assert skips == [] and "Only this sentence is text" in doc.content and "## Page 2" in doc.content


def test_a_scanned_or_blank_pdf_is_explained_not_guessed_at():
    docs, skips = importers.convert("scan.pdf", make_pdf(["", ""]))  # pages with no text layer, like a scan
    assert (
        docs == []
        and "no selectable text" in skips[0].reason
        and "scanned" in skips[0].reason
        and "OCR" in skips[0].reason
    )


def test_damaged_and_password_protected_pdfs_are_skipped_with_a_reason():
    docs, skips = importers.convert("broken.pdf", b"%PDF-1.4 not really")
    assert docs == [] and "could not be read" in skips[0].reason
    from pypdf import PdfReader, PdfWriter

    w = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf(["secret text here for the page"]))))
    w.encrypt("pw", algorithm="RC4-128")
    locked = io.BytesIO()
    w.write(locked)
    docs, skips = importers.convert("locked.pdf", locked.getvalue())
    assert docs == [] and "password protected" in skips[0].reason


def test_only_the_first_pages_of_a_very_long_pdf_are_read_and_it_says_so(monkeypatch):
    monkeypatch.setattr(importers, "MAX_PDF_PAGES", 2)
    [doc], _ = importers.convert("long.pdf", make_pdf(["page one text", "page two text", "page three text"]))
    assert "page two text" in doc.content and "page three text" not in doc.content
    assert "only the first 2 of 3 pages were imported" in doc.content


def test_a_pdf_goes_through_the_api_and_is_stored_as_a_searchable_document(client, auth):
    cid = make_collection(client, auth)["id"]
    r = post(client, auth, cid, **{"Spec.pdf": make_pdf(["Retries use exponential backoff of three attempts"])})
    assert r.status_code == 200 and [i["kind"] for i in r.json()["imported"]] == ["pdf"] and r.json()["skipped"] == []
    paths = {d["path"] for d in client.get(f"/api/knowledge/collections/{cid}/documents", headers=auth).json()}
    assert "Spec.md" in paths


# ---------------------------------------------------------------- Notion export zip
def notion_export() -> bytes:
    return zipped(
        {
            f"Handbook {ID_A}.md": f"# Handbook\n\nSee [Onboarding](Handbook%20{ID_A}/Onboarding%20{ID_B}.md) and [site](https://example.com/x) and [top](#top).\n",
            f"Handbook {ID_A}/Onboarding {ID_B}.md": f"# Onboarding\n\nBack to [Handbook](../Handbook%20{ID_A}.md). ![pic](img%20{ID_B}.png)\n",
            f"Handbook {ID_A}/Team {ID_B}.csv": "Name,Role\nAda,Eng\nBo,Ops\n",
            f"Handbook {ID_A}/Team {ID_B}_all.csv": "Name,Role\nAda,Eng\nBo,Ops\nCy,Dup\n",
            f"Handbook {ID_A}/img {ID_B}.png": b"\x89PNG....",
            "__MACOSX/._junk.md": "x",
            ".DS_Store": "x",
        }
    )


def test_a_notion_export_gets_clean_names_working_links_and_database_tables():
    docs, skips = importers.convert("Export.zip", notion_export())
    by = {d.path: d for d in docs}
    assert (
        set(by) == {"Handbook.md", "Handbook/Onboarding.md", "Handbook/Team.md"} and skips == []
    )  # ids gone; attachments, duplicates, junk left out
    assert "Cy" not in by["Handbook/Team.md"].content and "| Ada | Eng |" in by["Handbook/Team.md"].content
    assert "[Onboarding](Handbook/Onboarding.md)" in by["Handbook.md"].content  # the link now points at the saved page
    assert "[Handbook](../Handbook.md)" in by["Handbook/Onboarding.md"].content
    assert (
        "https://example.com/x" in by["Handbook.md"].content and "(#top)" in by["Handbook.md"].content
    )  # outside links are left alone
    assert all(d.kind in ("notion", "csv") for d in docs)


def test_names_that_collide_after_cleaning_are_kept_apart():
    docs, _ = importers.convert("x.zip", zipped({f"Notes {ID_A}.md": "a", f"Notes {ID_B}.md": "b"}))
    assert sorted(d.path for d in docs) == ["Notes (2).md", "Notes.md"]


def test_a_zip_inside_a_zip_is_opened_one_level_deep():
    inner = zipped({f"Page {ID_A}.md": "inside"})
    docs, _ = importers.convert("outer.zip", zipped({"Export-Part-1.zip": inner}))
    assert [d.content for d in docs] == ["inside"] and docs[0].path.endswith("Page.md")
    deep = zipped({"a.zip": zipped({"b.zip": zipped({"c.md": "too deep"})})})
    assert importers.convert("deep.zip", deep)[0] == []


# ---------------------------------------------------------------- hostile zips
def test_entries_cannot_escape_their_folder_and_hidden_files_are_ignored():
    docs, _ = importers.convert(
        "evil.zip", zipped({"../../etc/passwd.md": "x", "/abs/path.md": "ok", "a/../../b.md": "x", ".hidden/c.md": "x"})
    )
    assert [d.path for d in docs] == ["abs/path.md"]


def test_a_zip_with_too_many_files_or_too_much_data_is_refused_not_unpacked(monkeypatch):
    many = zipped({f"f{i}.md": "x" for i in range(importers.MAX_ZIP_ENTRIES + 1)})
    docs, skips = importers.convert("many.zip", many)
    assert docs == [] and "more than" in skips[0].reason
    monkeypatch.setattr(importers, "MAX_ZIP_TOTAL", 1000)
    docs, skips = importers.convert("big.zip", zipped({"a.md": "x" * 600, "b.md": "y" * 600}))
    assert docs == [] and "unpacks to more than" in skips[0].reason
    assert importers.convert("junk.zip", b"not a zip")[1][0].reason == "it is not a valid zip file"


def test_an_oversize_document_is_cut_not_stored_whole(monkeypatch):
    monkeypatch.setattr(importers, "MAX_TEXT", 50)
    [doc], _ = importers.convert("long.md", b"a" * 500)
    assert len(doc.content) < 200 and "cut here" in doc.content


# ---------------------------------------------------------------- the endpoint
def upload(**files):
    return [("files", (n, d, "application/octet-stream")) for n, d in files.items()]


def post(client, h, cid, **files):
    return client.post(f"/api/knowledge/collections/{cid}/import", headers=h, files=upload(**files))


def test_importing_files_stores_documents_reports_skips_and_makes_them_searchable(client, auth):
    cid = make_collection(client, auth)["id"]
    files = {
        "Runbook.docx": docx(para("Restart", "Heading1") + para("Drain the pod before restarting")),
        "Export.zip": notion_export(),
        "photo.png": b"\x89PNG",
        "notes.docx": b"nope",
    }
    r = post(client, auth, cid, **files)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["received"] == 4 and out["changed"] == 4
    assert {i["path"] for i in out["imported"]} == {
        "Runbook.md",
        "Handbook.md",
        "Handbook/Onboarding.md",
        "Handbook/Team.md",
    }
    assert {s["name"] for s in out["skipped"]} == {"photo.png", "notes.docx"}
    paths = {d["path"] for d in client.get(f"/api/knowledge/collections/{cid}/documents", headers=auth).json()}
    assert "Runbook.md" in paths and "Handbook/Team.md" in paths
    again = post(client, auth, cid, **{"Runbook.docx": files["Runbook.docx"]})
    assert again.json()["changed"] == 0  # the same file again changes nothing


def test_imported_text_is_masked_for_secrets_like_any_upload(client, auth, session):
    from repolens.models import Document

    cid = make_collection(client, auth)["id"]
    key = "ghp_" + "a" * 36
    r = post(client, auth, cid, **{"Notes.docx": docx(para(f"token {key} here"))})
    assert r.json()["secrets_redacted"] >= 1
    stored = session.query(Document).filter_by(repo_id=cid, path="Notes.md").one()
    assert key not in stored.content and "token" in stored.content  # the secret is gone, the rest is kept


def test_everything_skipped_is_still_a_clear_answer_and_changes_nothing(client, auth):
    cid = make_collection(client, auth)["id"]
    r = post(client, auth, cid, **{"a.png": b"x"})
    assert r.status_code == 200 and r.json()["imported"] == [] and r.json()["changed"] == 0
    assert r.json()["skipped"][0]["name"] == "a.png"


def test_only_people_who_may_write_can_import_and_workspaces_are_separate(client, auth):
    cid = make_collection(client, auth)["id"]
    client.post(
        "/api/auth/users", headers=auth, json={"email": "v@acme.io", "password": "viewer-pass-12", "role": "viewer"}
    )
    login = client.post("/api/auth/login", json={"email": "v@acme.io", "password": "viewer-pass-12"}).json()[
        "access_token"
    ]
    assert post(client, {"Authorization": "Bearer " + login}, cid, **{"a.md": b"# x"}).status_code == 403
    assert post(client, register(client, "z@other.io", "Other"), cid, **{"a.md": b"# x"}).status_code == 404
    url = f"/api/knowledge/collections/{cid}/import"
    assert client.post(url, files=upload(**{"a.md": b"x"})).status_code == 401
    assert client.post(url, headers=auth).status_code == 422  # no files at all
    too_many = {f"f{i}.md": b"# x" for i in range(21)}
    assert client.post(url, headers=auth, files=upload(**too_many)).status_code == 422


# ---------------------------------------------------------------- sizes: 25 MB a file
MB = 1024 * 1024


def test_a_file_of_25_megabytes_is_accepted_and_one_byte_more_is_not():
    assert importers.MAX_FILE == 25 * MB
    docs, skips = importers.convert("exactly.md", b"a" * (25 * MB))
    assert (
        len(docs) == 1 and skips == []
    )  # the text kept is cut to the document limit, the file itself is within bounds
    docs, skips = importers.convert("over.docx", b"a" * (25 * MB + 1))
    assert docs == [] and "larger than 25 MB" in skips[0].reason


def test_a_25_megabyte_upload_gets_through_the_api(client, auth):
    cid = make_collection(client, auth)["id"]
    r = post(client, auth, cid, **{"big.md": b"# Big\n\n" + b"word " * (5 * MB - 10)})  # just under 25 MB
    assert r.status_code == 200, r.text
    assert [i["path"] for i in r.json()["imported"]] == ["big.md"]
    over = post(client, auth, cid, **{"huge.md": b"a" * (25 * MB + 1)})
    assert over.status_code == 200 and "larger than 25 MB" in over.json()["skipped"][0]["reason"]


def test_only_imports_may_be_larger_than_the_normal_request_cap(client, auth, monkeypatch):
    from repolens import config

    monkeypatch.setenv("REPOLENS_MAX_BODY_MB", "1")
    monkeypatch.setenv("REPOLENS_MAX_UPLOAD_MB", "3")
    config.get_settings.cache_clear()
    cid = make_collection(client, auth)["id"]
    big = "x" * (2 * MB)
    refused = client.post("/api/knowledge/collections", headers=auth, json={"name": big})
    assert refused.status_code == 413 and "1 MB" in refused.json()["detail"]  # every other endpoint keeps the small cap
    assert post(client, auth, cid, **{"two.md": b"# x" + b"y" * (2 * MB)}).status_code == 200  # an import may be bigger
    refused = post(client, auth, cid, **{"four.md": b"y" * (4 * MB)})
    assert refused.status_code == 413 and "3 MB" in refused.json()["detail"]  # but not without limit


# ---------------------------------------------------------------- text from PDF and Word is kept literal
CODEY = "tensor([[1, 14350, 385]]) x = a[0](b) # a comment *starred* max_new_tokens"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("tensor([[1, 14350, 385]])", "tensor(\\[\\[1, 14350, 385\\]\\])"),
        ("see [1](2) and [link](http://x)", "see \\[1\\](2) and \\[link\\](http://x)"),
        ("# a comment", "\\# a comment"),
        ("## heading-like", "\\## heading-like"),
        ("> quoted", "\\> quoted"),
        ("- item", "\\- item"),
        ("+ item", "\\+ item"),
        ("1. first", "1\\. first"),
        ("2) second", "2\\) second"),
        ("---", "\\---"),
        ("=====", "\\====="),
        ("*bold* and `code`", "\\*bold\\* and \\`code\\`"),
        ("max_new_tokens and _private", "max_new_tokens and \\_private"),  # a word's inner underscores stay readable
        ("C:\\path\\file", "C:\\\\path\\\\file"),
        ("    indented code", "indented code"),
        ("plain words", "plain words"),
    ],
)
def test_text_is_made_literal_so_it_cannot_become_links_headings_or_lists(raw, expected):
    assert importers.escape_line(raw) == expected


def test_a_pdf_full_of_code_listings_makes_no_links_headings_or_lists():
    doc = importers.convert("Book.pdf", make_pdf([CODEY, "# Load the model", "1, 14350, 385]]"]))[0][0]
    from repolens.services.docs import extract_links, parse_markdown

    assert "[[" not in doc.content and "](" not in doc.content.replace("\\](", "")
    assert extract_links(doc.content) == []  # no phantom links to pages called "1, 14350, 385"
    titles = [c.symbol for c in parse_markdown("Book.md", doc.content)]
    assert [t for t in titles if t != "Book"] == ["Page 1", "Page 2", "Page 3"]  # only the pages are sections
    assert "Load the model" not in titles  # a "# comment" line in a listing is not a heading


def test_word_text_is_kept_literal_but_its_own_headings_and_lists_still_work():
    [doc], _ = importers.convert(
        "Notes.docx",
        docx(
            para("Setup", "Heading1")
            + para("tensor([[1, 2]]) and *x*")
            + para("# not a heading")
            + para("keep [a](b) literal", list_level=0)
        ),
    )
    assert "# Setup" in doc.content and "- keep \\[a\\](b) literal" in doc.content
    assert "tensor(\\[\\[1, 2\\]\\]) and \\*x\\*" in doc.content and "\\# not a heading" in doc.content


def test_csv_cells_are_literal_too():
    [t], _ = importers.convert("D.csv", b"Name,Note\nAda,see [[Page]] and [x](y)\n")
    assert "\\[\\[Page\\]\\]" in t.content


def test_the_link_detectors_ignore_escaped_brackets_and_still_find_real_links():
    from repolens.services import wiki
    from repolens.services.docs import extract_links

    assert extract_links("[x](y.md) and [[Some Page]]") == ["y.md", "wiki:Some Page"]
    assert extract_links("\\[x\\](y.md) and \\[\\[Some Page\\]\\]") == []
    assert wiki.MD_LINK.findall("\\[x\\](y)") == [] and wiki.WIKI_LINK.findall("\\[\\[a\\]\\]") == []
    assert wiki.MD_LINK.findall("[x](y)") == [("x", "y")] and wiki.WIKI_LINK.findall("[[a]]")


def test_the_wiki_shows_an_imported_pdf_without_broken_links_or_stray_headings(client, auth):
    cid = make_collection(client, auth)["id"]
    r = post(
        client, auth, cid, **{"Book.pdf": make_pdf([CODEY, "# Load the model", "output: [[ 1, 14350, 385, 4876 ]]"])}
    )
    assert r.status_code == 200 and [i["path"] for i in r.json()["imported"]] == ["Book.md"], r.text
    page = client.get("/api/wiki/page", headers=auth, params={"repo_id": cid, "path": "Book.md"}).json()
    assert page["unresolved_links"] == []  # these were "5 links point to pages that do not exist: 1, 14350, 385..."
    titles = [o["title"] for o in page["outline"]]
    assert [t for t in titles if t != "Book"] == ["Page 1", "Page 2", "Page 3"] and "Load the model" not in titles


# ---------------------------------------------------------------- a warning for very large documents (never a refusal)
def test_a_normal_document_gets_no_warning():
    for name, data in (
        ("Notes.md", b"# Notes\n\nshort"),
        ("Runbook.docx", docx(para("Hi") + para("there"))),
        ("Spec.pdf", make_pdf(["one page of text here", "and another"])),
    ):
        docs, skips = importers.convert(name, data)
        assert len(docs) == 1 and docs[0].warning == "" and skips == [], name


def test_a_long_pdf_is_imported_with_a_warning_that_says_why():
    pages = [f"Page number {i} has some text on it" for i in range(1, importers.LARGE_PAGES + 21)]
    [doc], skips = importers.convert("Big Book.pdf", make_pdf(pages))
    assert skips == [] and "## Page 120" in doc.content  # still imported in full
    assert "Big Book.pdf has 120 pages" in doc.warning
    assert "crowd search results" in doc.warning and "up to about 100 pages" in doc.warning


def test_a_pdf_longer_than_the_page_limit_says_what_was_left_out(monkeypatch):
    monkeypatch.setattr(importers, "MAX_PDF_PAGES", 3)
    monkeypatch.setattr(importers, "LARGE_PAGES", 2)
    [doc], _ = importers.convert("Long.pdf", make_pdf([f"text on page {i} here" for i in range(1, 6)]))
    assert "has 5 pages" in doc.warning and "Only the first 3 pages were imported" in doc.warning
    assert "page 4" not in doc.content


def test_very_long_text_and_text_cut_at_the_limit_are_both_flagged(monkeypatch):
    [doc], _ = importers.convert("Wall.txt", b"word " * 70_000)  # about 350,000 characters
    assert "Wall.txt is very long" in doc.warning and "cut off" not in doc.warning
    monkeypatch.setattr(importers, "MAX_TEXT", 1000)
    [cut], _ = importers.convert("Huge.md", b"word " * 1000)
    assert "cut off" in cut.warning and "cut here" in cut.content


def test_a_zip_that_adds_hundreds_of_pages_gets_one_warning_for_the_whole_zip(monkeypatch):
    monkeypatch.setattr(importers, "LARGE_ZIP_DOCS", 3)
    docs, _ = importers.convert("Everything.zip", zipped({f"p{i}.md": f"# P{i}" for i in range(5)}))
    warned = [d.warning for d in docs if d.warning]
    assert len(docs) == 5 and len(warned) == 1 and "Everything.zip added 5 pages at once" in warned[0]
    assert "only the sections your team uses" in warned[0]


def test_the_import_answer_carries_the_warnings_and_still_imports(client, auth):
    cid = make_collection(client, auth)["id"]
    pages = [f"Page number {i} has some text on it" for i in range(1, importers.LARGE_PAGES + 6)]
    r = post(client, auth, cid, **{"Book.pdf": make_pdf(pages), "Short.md": b"# Short\n\nfine"})
    out = r.json()
    assert r.status_code == 200 and {i["path"] for i in out["imported"]} == {
        "Book.md",
        "Short.md",
    }  # a warning is not a refusal
    assert len(out["warnings"]) == 1 and "Book.pdf has 105 pages" in out["warnings"][0]
    assert post(client, auth, cid, **{"Only.md": b"# Only"}).json()["warnings"] == []
