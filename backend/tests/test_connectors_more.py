import io
import json
import zipfile

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from repolens.models import Document, WikiSource
from repolens.services import connectors
from repolens.services.connectors import gdocs, notion, sharepoint
from tests.conftest import register


@pytest.fixture(autouse=True)
def clean():
    yield
    connectors.transports.clear()


def docs_of(session, repo_id):
    return {d.source_id: d for d in session.query(Document).filter_by(repo_id=repo_id)}


# ------------------------------------------------------------------------------------------------ Notion
def rt(text, **ann):
    return [{"plain_text": text, "annotations": ann}]


PAGE_ID = "0123456789abcdef0123456789abcdef"
CHILD_ID = "fedcba9876543210fedcba9876543210"


def notion_api(state):
    def handler(r: httpx.Request) -> httpx.Response:
        state["calls"].append((r.method, r.url.path, r.headers.get("notion-version"), r.headers.get("authorization")))
        p = r.url.path
        if p == "/v1/users/me":
            return httpx.Response(200, json={"name": "RepoLens", "bot": {"workspace_name": "Acme"}})
        if p == "/v1/search":
            return httpx.Response(200, json={"results": [state["root"], state["child"]], "has_more": False})
        if p == f"/v1/pages/{PAGE_ID}":
            return httpx.Response(200, json=state["root"])
        if p == f"/v1/blocks/{PAGE_ID}/children":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": "b1", "type": "heading_1", "heading_1": {"rich_text": rt("Runbook")}},
                        {
                            "id": "b2",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": rt("Restart ", bold=False)
                                + rt("the worker", bold=True)
                                + rt(" first", italic=True)
                            },
                        },
                        {
                            "id": "b3",
                            "type": "bulleted_list_item",
                            "has_children": True,
                            "bulleted_list_item": {"rich_text": rt("check logs")},
                        },
                        {"id": "b4", "type": "to_do", "to_do": {"rich_text": rt("page oncall"), "checked": True}},
                        {
                            "id": "b5",
                            "type": "code",
                            "code": {"language": "bash", "rich_text": rt("systemctl restart worker")},
                        },
                        {"id": "b6", "type": "child_page", "child_page": {"title": "Details"}},
                        {"id": "b7", "type": "table", "has_children": True, "table": {}},
                    ],
                    "has_more": False,
                },
            )
        if p == "/v1/blocks/b3/children":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "b3a",
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {"rich_text": rt("look for OOM")},
                        }
                    ],
                    "has_more": False,
                },
            )
        if p == "/v1/blocks/b7/children":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"type": "table_row", "table_row": {"cells": [rt("svc"), rt("owner")]}},
                        {"type": "table_row", "table_row": {"cells": [rt("api"), rt("ana")]}},
                    ],
                    "has_more": False,
                },
            )
        if p == f"/v1/blocks/{CHILD_ID}/children":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "c1",
                            "type": "paragraph",
                            "paragraph": {"rich_text": rt("Child body, key ghp_" + "b" * 36)},
                        }
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(404 if "pages" in p else 200, json={"results": [], "has_more": False})

    return httpx.MockTransport(handler)


def notion_state():
    return {
        "calls": [],
        "root": {
            "id": "01234567-89ab-cdef-0123-456789abcdef",
            "url": "https://notion.so/Team-wiki",
            "last_edited_time": "2026-05-01T10:00:00.000Z",
            "parent": {"type": "workspace"},
            "properties": {"title": {"type": "title", "title": rt("Team wiki")}},
        },
        "child": {
            "id": "fedcba98-7654-3210-fedc-ba9876543210",
            "url": "https://notion.so/Details",
            "last_edited_time": "2026-05-02T10:00:00.000Z",
            "parent": {"type": "page_id", "page_id": "01234567-89ab-cdef-0123-456789abcdef"},
            "properties": {"Name": {"type": "title", "title": rt("Details")}},
        },
    }


def test_notion_pages_to_markdown_with_hierarchy(client, auth, session):
    state = notion_state()
    connectors.transports["notion"] = notion_api(state)
    body = {"config": {}, "secret": "secret_" + "x" * 30, "name": "Team Notion"}
    assert (
        client.post("/api/wiki/connectors/notion/check", headers=auth, json=body).json()["detail"]
        == "Connected to Notion workspace Acme as RepoLens"
    )
    r = client.post("/api/wiki/connectors/notion/import", headers=auth, json=body)
    assert r.status_code == 202, r.text
    docs = docs_of(session, r.json()["repo_id"])
    root, child = docs[PAGE_ID], docs[CHILD_ID]
    assert (
        root.path == "notion/team-wiki.md"
        and child.path == "notion/team-wiki/details.md"
        and child.parent_path == root.path
    )
    c = root.content
    assert "## Runbook" in c and "Restart **the worker** *first*" in c
    assert (
        "- check logs" in c
        and "  - look for OOM" in c
        and "- [x] page oncall" in c
        and "```bash\nsystemctl restart worker\n```" in c
    )
    assert "- [[Details]]" in c and "| svc | owner |" in c and "|---|---|" in c
    assert "ghp_" not in child.content and "Child body" in child.content
    assert all(call[2] == "2022-06-28" and call[3].startswith("Bearer secret_") for call in state["calls"])


def test_notion_root_page_option_and_errors(client, auth, session):
    state = notion_state()
    connectors.transports["notion"] = notion_api(state)
    r = client.post(
        "/api/wiki/connectors/notion/import",
        headers=auth,
        json={"config": {"root_page": f"https://www.notion.so/Team-wiki-{PAGE_ID}"}, "secret": "secret_" + "x" * 30},
    )
    docs = docs_of(session, r.json()["repo_id"])
    assert (
        PAGE_ID in docs and CHILD_ID not in docs
    )  # no child_page block id matches in this fake, only the root is fetched
    assert (
        client.post(
            "/api/wiki/connectors/notion/import",
            headers=auth,
            json={"config": {"root_page": "nonsense"}, "secret": "x" * 30},
        ).status_code
        == 400
    )
    assert (
        client.post("/api/wiki/connectors/notion/check", headers=auth, json={"config": {}, "secret": "short"}).json()[
            "ok"
        ]
        is False
    )
    connectors.transports["notion"] = httpx.MockTransport(
        lambda r: httpx.Response(401, json={"message": "API token is invalid. secret_leaky"})
    )
    out = client.post(
        "/api/wiki/connectors/notion/check", headers=auth, json={"config": {}, "secret": "secret_" + "x" * 30}
    ).json()
    assert out["ok"] is False and "rejected the token" in out["error"] and "leaky" not in out["error"]


# ------------------------------------------------------------------------------------------------ Google Docs
def service_account(email="bot@proj.iam.gserviceaccount.com"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return json.dumps(
        {
            "type": "service_account",
            "client_email": email,
            "private_key": pem,
            "token_uri": "https://evil.example/steal",
        }
    ), key.public_key()


def google_api(state, public_key):
    import jwt

    def handler(r: httpx.Request) -> httpx.Response:
        state["calls"].append((r.method, str(r.url).split("?")[0]))
        if r.url.host == "oauth2.googleapis.com":
            form = dict(x.split("=", 1) for x in r.content.decode().split("&"))
            claims = jwt.decode(
                form["assertion"].replace("%2E", "."),
                public_key,
                algorithms=["RS256"],
                audience="https://oauth2.googleapis.com/token",
            )
            state["claims"] = claims
            return httpx.Response(200, json={"access_token": "ya29.test"})
        assert r.headers["authorization"] == "Bearer ya29.test"
        p, q = r.url.path, dict(r.url.params)
        if p == "/drive/v3/files/FOLDER12345":
            return httpx.Response(
                200, json={"id": "FOLDER12345", "name": "Eng docs", "mimeType": "application/vnd.google-apps.folder"}
            )
        if p == "/drive/v3/files" and "FOLDER12345" in q["q"]:
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": "doc1",
                            "name": "On-call guide",
                            "mimeType": "application/vnd.google-apps.document",
                            "modifiedTime": "2026-05-01T10:00:00Z",
                            "webViewLink": "https://docs.google.com/document/d/doc1",
                        },
                        {"id": "sub1", "name": "Runbooks", "mimeType": "application/vnd.google-apps.folder"},
                        {"id": "sheet", "name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet"},
                        {
                            "id": "md1",
                            "name": "README.md",
                            "mimeType": "text/markdown",
                            "modifiedTime": "2026-05-02T10:00:00Z",
                            "size": "20",
                        },
                    ]
                },
            )
        if p == "/drive/v3/files" and "sub1" in q["q"]:
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": "doc2",
                            "name": "Restart db",
                            "mimeType": "application/vnd.google-apps.document",
                            "modifiedTime": "2026-05-03T10:00:00Z",
                        }
                    ]
                },
            )
        if p == "/drive/v3/files/doc1/export":
            return httpx.Response(
                200,
                text="<html><head><style>p{}</style></head><body><h1>On-call</h1><p>Page the <b>primary</b> first.</p></body></html>",
            )
        if p == "/drive/v3/files/doc2/export":
            return httpx.Response(500)
        if p == "/drive/v3/files/md1":
            return httpx.Response(200, text="# Readme\nplain markdown")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_google_docs_via_service_account(client, auth, session):
    secret, pub = service_account()
    state = {"calls": []}
    connectors.transports["gdocs"] = google_api(state, pub)
    body = {
        "config": {"folder": "https://drive.google.com/drive/folders/FOLDER12345?usp=sharing"},
        "secret": secret,
        "name": "Eng Drive",
    }
    assert (
        client.post("/api/wiki/connectors/gdocs/check", headers=auth, json=body).json()["detail"]
        == "Connected to Google Drive folder “Eng docs”"
    )
    r = client.post("/api/wiki/connectors/gdocs/import", headers=auth, json=body)
    assert r.status_code == 202, r.text
    docs = docs_of(session, r.json()["repo_id"])
    assert set(docs) == {"doc1", "md1"}  # the spreadsheet is skipped; doc2's export failure does not fail the folder
    assert (
        docs["doc1"].path == "gdocs/on-call-guide.md"
        and "Page the" in docs["doc1"].content
        and "**primary**" in docs["doc1"].content
    )
    assert docs["md1"].content.count("# Readme") == 1 and docs["doc1"].source_url.startswith("https://docs.google.com/")
    assert state["claims"]["iss"] == "bot@proj.iam.gserviceaccount.com" and "drive.readonly" in state["claims"]["scope"]
    assert all(
        "evil.example" not in url for _, url in state["calls"]
    )  # the token endpoint is fixed, not read from the key file
    src = session.query(WikiSource).filter_by(repo_id=r.json()["repo_id"]).one()
    assert "PRIVATE KEY" not in src.secret_encrypted and "PRIVATE KEY" not in json.dumps(
        client.get("/api/wiki/sources", headers=auth).json()
    )


def test_google_docs_validation(client, auth):
    for config, secret in (
        ({"folder": "not a folder!"}, "{}"),
        ({"folder": "FOLDER12345"}, "not json"),
        ({"folder": "FOLDER12345"}, json.dumps({"type": "authorized_user"})),
    ):
        r = (
            client.post(
                "/api/wiki/connectors/gdocs/check", headers=auth, json={"config": config, "secret": secret}
            ).json()
            if "!" not in config["folder"]
            else None
        )
        if r is not None:
            assert r["ok"] is False
    assert (
        client.post(
            "/api/wiki/connectors/gdocs/import", headers=auth, json={"config": {"folder": "x!"}, "secret": "{}"}
        ).status_code
        == 400
    )
    assert gdocs.folder_id_from("https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjK") == "1AbCdEfGhIjK"


# ------------------------------------------------------------------------------------------------ SharePoint
def make_docx(paragraphs, table=None, doctype=False):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = ""
    for style, text in paragraphs:
        num = '<w:numPr><w:ilvl w:val="0"/></w:numPr>' if style == "ListParagraph" else ""
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/>{num}</w:pPr>' if style else ""
        body += f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"
    if table:
        body += (
            "<w:tbl>"
            + "".join(
                "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
                for row in table
            )
            + "</w:tbl>"
        )
    dt = '<!DOCTYPE x [<!ENTITY a "b">]>' if doctype else ""
    xml = '<?xml version="1.0"?>' + dt + '<w:document xmlns:w="' + w + '"><w:body>' + body + "</w:body></w:document>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def graph_api(state):
    def handler(r: httpx.Request) -> httpx.Response:
        state["calls"].append((r.method, str(r.url)))
        p = r.url.path
        if r.url.host == "login.microsoftonline.com":
            assert "/contoso.onmicrosoft.com/oauth2/v2.0/token" in p
            return httpx.Response(200, json={"access_token": "graph-token"})
        assert r.headers["authorization"] == "Bearer graph-token"
        if p == "/v1.0/sites/contoso.sharepoint.com:/sites/Engineering":
            return httpx.Response(200, json={"id": "site-1", "displayName": "Engineering"})
        if p == "/v1.0/sites/site-1/drive":
            return httpx.Response(200, json={"id": "drv"})
        if (
            p == "/v1.0/drives/drv/root:/Shared%20Documents/Runbooks:/children"
            or p == "/v1.0/drives/drv/root:/Shared Documents/Runbooks:/children"
        ):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "f1",
                            "name": "Restart.docx",
                            "size": 1000,
                            "lastModifiedDateTime": "2026-05-01T10:00:00Z",
                            "webUrl": "https://contoso.sharepoint.com/r.docx",
                        },
                        {"id": "f2", "name": "notes.md", "size": 20, "lastModifiedDateTime": "2026-05-02T10:00:00Z"},
                        {"id": "fold", "name": "Old", "folder": {}},
                        {"id": "f3", "name": "photo.png", "size": 10},
                        {"id": "f4", "name": "evil.docx", "size": 100},
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/page2",
                },
            )
        if p == "/v1.0/page2":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "f5", "name": "page.html", "size": 50, "lastModifiedDateTime": "2026-05-03T10:00:00Z"}
                    ]
                },
            )
        if p == "/v1.0/drives/drv/items/fold/children":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "f6", "name": "legacy.txt", "size": 5, "lastModifiedDateTime": "2026-01-01T00:00:00Z"}
                    ]
                },
            )
        content = {
            "f1": make_docx(
                [("Heading1", "Restart procedure"), (None, "Drain the node."), ("ListParagraph", "step")],
                [["svc", "owner"], ["db", "ana"]],
            ),
            "f2": b"plain notes",
            "f4": make_docx([(None, "x")], doctype=True),
            "f5": b"<html><body><h2>Policy</h2><p>No friday deploys</p></body></html>",
            "f6": b"legacy text",
        }
        if p.startswith("/v1.0/drives/drv/items/") and p.endswith("/content"):
            return httpx.Response(200, content=content[p.split("/")[-2]])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_sharepoint_library_with_docx_html_and_folders(client, auth, session):
    state = {"calls": []}
    connectors.transports["sharepoint"] = graph_api(state)
    cfg = {
        "tenant": "contoso.onmicrosoft.com",
        "client_id": "00000000-0000-0000-0000-000000000000",
        "site": "https://contoso.sharepoint.com:/sites/Engineering",
        "folder": "Shared Documents/Runbooks",
    }
    body = {"config": cfg, "secret": "client-secret-value", "name": "Eng SharePoint"}
    assert (
        client.post("/api/wiki/connectors/sharepoint/check", headers=auth, json=body).json()["detail"]
        == "Connected to SharePoint site “Engineering”"
    )
    r = client.post("/api/wiki/connectors/sharepoint/import", headers=auth, json=body)
    assert r.status_code == 202, r.text
    docs = docs_of(session, r.json()["repo_id"])
    assert set(docs) == {
        "f1",
        "f2",
        "f5",
        "f6",
    }  # png skipped; the hostile (DOCTYPE) docx is skipped without failing the library
    assert (
        "## Restart procedure" in docs["f1"].content
        and "Drain the node." in docs["f1"].content
        and "- step" in docs["f1"].content
        and "| db | ana |" in docs["f1"].content
    )
    assert "## Policy" in docs["f5"].content and docs["f6"].path == "sharepoint/old/legacy.md"
    from urllib.parse import urlparse

    assert {urlparse(u).hostname for _, u in state["calls"]} == {"login.microsoftonline.com", "graph.microsoft.com"}


def test_sharepoint_validation_and_docx_safety():
    c = (
        connectors.get("sharepoint")
        if "sharepoint" in connectors.REGISTRY
        else (connectors.load_all(), connectors.get("sharepoint"))[1]
    )
    good = {
        "tenant": "contoso.onmicrosoft.com",
        "client_id": "00000000-0000-0000-0000-000000000000",
        "site": "contoso.sharepoint.com",
    }
    assert c.validate(good)["folder"] == ""
    for bad in (
        {**good, "site": "evil.example.com"},
        {**good, "site": "contoso.sharepoint.com:/sites/../x"},
        {**good, "tenant": "a b"},
        {**good, "client_id": "nope"},
        {**good, "folder": "../etc"},
    ):
        with pytest.raises(connectors.ConnectorError):
            c.validate(bad)
    with pytest.raises(connectors.ConnectorError, match="unsupported XML"):
        sharepoint.docx_to_markdown(make_docx([(None, "x")], doctype=True))
    with pytest.raises(connectors.ConnectorError, match="could not be opened"):
        sharepoint.docx_to_markdown(b"not a zip")


def test_all_connectors_are_listed_with_secret_fields_marked(client, auth):
    kinds = {c["kind"]: c for c in client.get("/api/wiki/connectors", headers=auth).json()}
    assert {"jira", "slack", "notion", "gdocs", "sharepoint"} <= set(kinds)
    for k in ("slack", "notion", "gdocs", "sharepoint"):
        assert sum(f["secret"] for f in kinds[k]["fields"]) == 1
    assert not any(f["secret"] for f in kinds["jira"]["fields"])
    member = register(client, "x@other.io", "Other")  # a different tenant's admin still cannot touch another org's data
    assert client.get("/api/wiki/sources", headers=member).json() == []
    assert notion.page_id_from("https://notion.so/a-0123456789abcdef0123456789abcdef?pvs=4") == PAGE_ID
