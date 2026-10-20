import base64
import json

import httpx
import pytest

from repolens.config import get_settings
from repolens.devtools.fake_confluence import FakeConfluence
from repolens.models import ConfluenceConnection, Document, WikiSource
from repolens.services import wiki_import as wi
from repolens.services.confluence import (
    ConfluenceClient,
    ConfluenceError,
    parse_confluence_url,
    storage_to_markdown,
)
from tests.conftest import register
from tests.test_knowledge import edge_set, graph

TOKEN = "good-token"
CLOUD = "https://fake.atlassian.net/wiki/spaces/ENG/pages/100/Engineering+Handbook"
SPACE = "https://fake.atlassian.net/wiki/spaces/ENG/overview"


# ------------------------------------------------------------------ storage -> markdown
def test_converter_handles_headings_links_macros_tables_lists_and_tasks():
    md = storage_to_markdown(
        FakeConfluence().pages["101"]["body"]
        + FakeConfluence().pages["102"]["body"]
        + FakeConfluence().pages["103"]["body"]
    )
    assert "## Triage" in md and "**recent deploys**" in md and "`UserProfileService.get_profile`" in md
    assert "> **Warning:**\n> Never restart the gateway during peak hours." in md
    assert "[[Profile Service]]" in md and "[[Retired Runbook]]" in md  # page links become wiki links
    assert "```bash\nkubectl rollout undo deploy/api\n```" in md
    assert "| Component | Owner |\n|---|---|\n| api | team-platform |" in md
    assert "- Missing users return a default profile" in md and "- [ ] Review rotation quarterly" in md


def test_converter_details():
    md = storage_to_markdown(
        '<p>See <ac:link ac:anchor="setup"><ri:page ri:content-title="Guide" /><ac:plain-text-link-body><![CDATA[the guide]]></ac:plain-text-link-body></ac:link> '
        'by <ac:link><ri:user ri:account-id="abcdef123456789" /></ac:link> ticket <ac:structured-macro ac:name="jira"><ac:parameter ac:name="key">BUG-7</ac:parameter></ac:structured-macro></p>'
        '<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[if a < b && c > d: pass]]></ac:plain-text-body></ac:structured-macro>'
        "<ul><li>a<ul><li>b</li></ul></li></ul><ol><li>x</li><li>y</li></ol><p>1 &lt; 2 &amp; 3</p>"
        '<ac:structured-macro ac:name="toc" /><ac:structured-macro ac:name="mystery"><ac:rich-text-body><p>still shown</p></ac:rich-text-body></ac:structured-macro>'
    )
    assert "[[Guide#setup|the guide]]" in md and "@abcdef123456" in md and "BUG-7" in md
    assert "if a < b && c > d: pass" in md  # CDATA content is literal, not HTML
    assert "- a\n  - b" in md and "1. x\n2. y" in md and "1 < 2 & 3" in md
    assert "still shown" in md and "toc" not in md.lower()


def test_converter_drops_active_content_and_tolerates_garbage():
    md = storage_to_markdown(
        '<p>hi<script>alert(1)</script><style>p{}</style><iframe src="x"></iframe></p><b>unclosed <i>tags'
    )
    assert "alert" not in md and "iframe" not in md and "hi" in md and "unclosed" in md
    assert storage_to_markdown("") == "" and storage_to_markdown(None) == ""  # type: ignore[arg-type]
    big = storage_to_markdown("<p>" + "word " * 200_000 + "</p>")
    assert len(big) < 310_000 and big.endswith("[content truncated]_")


def test_table_edge_cases():
    md = storage_to_markdown("<table><tr><td>a | b</td><td></td></tr><tr><td>c</td></tr></table>")
    assert (
        "a \\| b" in md and md.count("\n|---") == 1 and "Column 1" in md
    )  # no header row -> synthetic header, ragged rows padded


# ------------------------------------------------------------------ URL parsing
@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://acme.atlassian.net/wiki/spaces/ENG/pages/12345/Incident+Response",
            ("https://acme.atlassian.net", "ENG", "12345", "Incident Response"),
        ),
        ("https://acme.atlassian.net/wiki/spaces/ENG/overview", ("https://acme.atlassian.net", "ENG", "", "")),
        ("https://acme.atlassian.net/wiki/spaces/ENG", ("https://acme.atlassian.net", "ENG", "", "")),
        ("https://wiki.corp.io/display/OPS/Run+Book", ("https://wiki.corp.io", "OPS", "", "Run Book")),
        ("https://wiki.corp.io/display/OPS", ("https://wiki.corp.io", "OPS", "", "")),
        (
            "https://wiki.corp.io/confluence/pages/viewpage.action?pageId=99",
            ("https://wiki.corp.io/confluence", "", "99", ""),
        ),
        ("https://wiki.corp.io/spaces/viewspace.action?key=DOC", ("https://wiki.corp.io", "DOC", "", "")),
    ],
)
def test_parse_confluence_urls(url, expected):
    r = parse_confluence_url(url)
    assert (r.base_url, r.space_key, r.page_id, r.title) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not a url",
        "ftp://x/wiki/spaces/A",
        "https://acme.atlassian.net/wiki/x/AbCd",
        "https://example.com/some/page",
    ],
)
def test_unrecognised_urls_are_rejected_with_a_helpful_message(bad):
    with pytest.raises(ConfluenceError):
        parse_confluence_url(bad)


# ------------------------------------------------------------------ client
def make_transport(fake):
    def handler(r: httpx.Request) -> httpx.Response:
        status, body, headers = fake.handle(r.method, str(r.url), dict(r.headers), r.content)
        if isinstance(body, str):
            return httpx.Response(status, text=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    return httpx.MockTransport(handler)


@pytest.fixture
def fake(monkeypatch):
    f = FakeConfluence()
    monkeypatch.setattr(wi, "confluence_transport", make_transport(f))
    return f


def test_cloud_uses_basic_auth_and_datacenter_uses_bearer(fake, monkeypatch):
    ConfluenceClient(
        "https://fake.atlassian.net", "cloud", TOKEN, "bot@acme.io", transport=make_transport(fake)
    ).current_user()
    dc = FakeConfluence("datacenter", token="pat-123")
    ConfluenceClient("https://fake.corp.io", "datacenter", "pat-123", transport=make_transport(dc)).current_user()
    assert any(p.endswith("/wiki/rest/api/user/current") for _, p in fake.requests) and any(
        p == "/rest/api/user/current" for _, p in dc.requests
    )


@pytest.mark.parametrize(
    "code,needle", [(401, "Authentication failed"), (403, "no permission"), (404, "not found"), (429, "rate limit")]
)
def test_errors_are_translated_and_never_echo_credentials(fake, code, needle):
    c = ConfluenceClient(
        "https://fake.atlassian.net",
        "cloud",
        TOKEN,
        "bot@acme.io",
        transport=make_transport(fake),
        sleep=lambda s: None,
    )
    fake.fail_next = [code] * 3
    with pytest.raises(ConfluenceError) as e:
        c.get_page("100")
    assert needle in str(e.value) and TOKEN not in str(e.value)


def test_rate_limit_is_retried_and_server_errors_once(fake):
    sleeps = []
    c = ConfluenceClient(
        "https://fake.atlassian.net", "cloud", TOKEN, "bot@acme.io", transport=make_transport(fake), sleep=sleeps.append
    )
    fake.fail_next = [429, 429]
    assert c.get_page("100")["title"] == "Engineering Handbook" and len(sleeps) == 2
    fake.fail_next = [503]
    assert c.get_page("101")["title"] == "Incident Response"


def test_wrong_credentials_and_non_json_and_unsafe_urls(fake):
    bad = ConfluenceClient(
        "https://fake.atlassian.net", "cloud", "wrong", "bot@acme.io", transport=make_transport(fake)
    )
    with pytest.raises(ConfluenceError, match="Authentication"):
        bad.current_user()
    html_page = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>login</html>"))
    with pytest.raises(ConfluenceError, match="non-JSON"):
        ConfluenceClient("https://fake.atlassian.net", "cloud", TOKEN, "a@b.c", transport=html_page).current_user()


def test_base_url_must_be_https_and_public_outside_dev(monkeypatch):
    monkeypatch.setenv("REPOLENS_ENV", "prod")
    get_settings.cache_clear()
    for bad in ("http://wiki.corp.io", "https://127.0.0.1", "https://10.1.2.3/wiki", "https://localhost"):
        with pytest.raises(ConfluenceError):
            ConfluenceClient(bad, "datacenter", "x")


# ------------------------------------------------------------------ connection API
def connect(client, auth, token=TOKEN, base="https://fake.atlassian.net"):
    r = client.put(
        "/api/wiki/confluence/connection",
        headers=auth,
        json={"base_url": base, "deployment": "cloud", "email": "bot@acme.io", "token": token},
    )
    assert r.status_code == 200, r.text
    return r


def test_connection_secret_is_encrypted_hidden_and_admin_only(client, auth, fake, session):
    r = connect(client, auth)
    assert TOKEN not in json.dumps(r.json()) and "token" not in r.json()
    assert not session.query(ConfluenceConnection).one().secret_encrypted.startswith("good")
    assert client.post("/api/wiki/confluence/test", headers=auth).json() == {"ok": True, "user": "RepoLens Bot"}
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"}
    )
    tok = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "member-pass-1"}).json()[
        "access_token"
    ]
    member = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/wiki/confluence/connection", headers=member).json() == {
        "connected": True
    }  # no URL / email for members
    assert (
        client.put(
            "/api/wiki/confluence/connection",
            headers=member,
            json={"base_url": "https://x.atlassian.net", "email": "a@b.c", "token": "abcd"},
        ).status_code
        == 403
    )
    assert client.post("/api/wiki/confluence/import", headers=member, json={"url": CLOUD}).status_code == 403


def test_connection_validation_and_bad_credentials(client, auth, fake):
    assert (
        client.put(
            "/api/wiki/confluence/connection",
            headers=auth,
            json={"base_url": "https://x.atlassian.net", "token": "abcd"},
        ).status_code
        == 400
    )  # cloud needs email
    assert (
        client.put(
            "/api/wiki/confluence/connection",
            headers=auth,
            json={"base_url": "ftp://wiki.example.com", "email": "a@b.c", "token": "abcd"},
        ).status_code
        == 400
    )
    connect(client, auth, token="wrong-token")
    r = client.post("/api/wiki/confluence/test", headers=auth).json()
    assert r["ok"] is False and "Authentication failed" in r["error"] and "wrong-token" not in json.dumps(r)


def test_import_requires_a_connection_and_a_readable_url(client, auth, fake):
    assert client.post("/api/wiki/confluence/import", headers=auth, json={"url": CLOUD}).status_code == 400
    connect(client, auth, token="wrong-token")
    r = client.post("/api/wiki/confluence/import", headers=auth, json={"url": CLOUD})
    assert r.status_code == 502 and "Authentication" in r.text and "wrong-token" not in r.text
    connect(client, auth)
    assert (
        client.post(
            "/api/wiki/confluence/import",
            headers=auth,
            json={"url": "https://fake.atlassian.net/wiki/spaces/ENG/pages/999/Nope"},
        ).status_code
        == 502
    )
    assert (
        client.post("/api/wiki/confluence/import", headers=auth, json={"url": "https://example.com/x"}).status_code
        == 400
    )
    fake.denied_spaces.add("FIN")
    assert (
        client.post(
            "/api/wiki/confluence/import",
            headers=auth,
            json={"url": "https://fake.atlassian.net/wiki/spaces/FIN/overview"},
        ).status_code
        == 502
    )
    assert client.get("/api/wiki/sources", headers=auth).json() == []  # failures create nothing


# ------------------------------------------------------------------ import
def do_import(client, auth, url=CLOUD, **kw):
    r = client.post("/api/wiki/confluence/import", headers=auth, json={"url": url, **kw})
    assert r.status_code == 202, r.text
    return r.json()


def docs(session, repo_id):
    session.expire_all()
    return {d.path: d for d in session.query(Document).filter_by(repo_id=repo_id)}


def test_tree_import_builds_pages_hierarchy_links_and_graph(client, auth, fake, session):
    connect(client, auth)
    prev = client.post("/api/wiki/confluence/preview", headers=auth, json={"url": CLOUD}).json()
    assert prev["title"] == "Engineering Handbook" and prev["suggested_mode"] == "tree"
    imp = do_import(client, auth)
    assert (
        imp["mode"] == "tree"
        and imp["visibility"] == "restricted"
        and imp["name"] == "Confluence: Engineering Handbook"
    )
    d = docs(session, imp["repo_id"])
    assert set(d) == {
        "eng/engineering-handbook.md",
        "eng/engineering-handbook/incident-response.md",
        "eng/engineering-handbook/profile-service.md",
        "eng/engineering-handbook/profile-service/on-call-rotation.md",
    }
    root, inc = d["eng/engineering-handbook.md"], d["eng/engineering-handbook/incident-response.md"]
    assert root.source_type == "confluence" and root.source_id == "100" and root.source_version == 1
    assert root.source_url == "https://fake.atlassian.net/wiki/spaces/ENG/pages/100/Engineering+Handbook"
    assert (
        d["eng/engineering-handbook/profile-service/on-call-rotation.md"].parent_path
        == "eng/engineering-handbook/profile-service.md"
        and inc.parent_path == "eng/engineering-handbook.md"
    )
    assert (
        root.content.startswith('---\ntitle: "Engineering Handbook"\nsource: confluence')
        and "[[Incident Response]]" in root.content
    )
    src = session.query(WikiSource).one()
    assert (src.last_status, src.pages_total, src.mode, src.space_key, src.root_page_id) == (
        "completed",
        4,
        "tree",
        "ENG",
        "100",
    )
    repo = client.get(f"/api/repositories/{imp['repo_id']}", headers=auth).json()
    assert repo["status"] == "ready" and repo["source"] == "docs" and repo["visibility"] == "restricted"
    edges = edge_set(graph(client, auth, types="file,doc,section,function,class", limit=200))
    # wiki links resolve by *title*, including the custom-label link, across files
    assert ("Welcome", "LINKS_TO", "eng/engineering-handbook/incident-response.md") in edges
    assert ("Welcome", "LINKS_TO", "eng/engineering-handbook/profile-service.md") in edges
    # page hierarchy
    assert ("eng/engineering-handbook.md", "CONTAINS", "eng/engineering-handbook/incident-response.md") in edges
    assert (
        "eng/engineering-handbook/profile-service.md",
        "CONTAINS",
        "eng/engineering-handbook/profile-service/on-call-rotation.md",
    ) in edges
    assert (
        "eng/engineering-handbook.md",
        "CONTAINS",
        "eng/engineering-handbook/profile-service/on-call-rotation.md",
    ) not in edges  # direct parent only


def test_broken_links_are_recorded_on_the_page_node(client, auth, fake):
    connect(client, auth)
    imp = do_import(client, auth)
    node = next(
        n for n in client.get("/api/graph/nodes", headers=auth, params={"q": "incident-response", "type": "doc"}).json()
    )
    assert node["meta"]["broken_links"] == [{"section": "Deleted users", "link": "wiki:Retired Runbook"}]
    assert node["meta"]["source_type"] == "confluence" and node["meta"]["title"] == "Incident Response"
    assert imp["repo_id"] == node["repo_id"]


def test_space_and_page_modes(client, auth, fake, session):
    connect(client, auth)
    space = do_import(client, auth, SPACE)
    assert space["mode"] == "space" and len(docs(session, space["repo_id"])) == 4  # all ENG pages, FIN excluded
    page = do_import(client, auth, CLOUD, mode="page", name="just the handbook")
    assert set(docs(session, page["repo_id"])) == {"eng/engineering-handbook.md"}
    assert (
        client.post("/api/wiki/confluence/import", headers=auth, json={"url": SPACE, "mode": "tree"}).status_code == 400
    )
    assert (
        client.post("/api/wiki/confluence/import", headers=auth, json={"url": CLOUD, "mode": "bogus"}).status_code
        == 422
    )


def test_names_are_made_unique_and_visibility_can_be_org(client, auth, fake):
    connect(client, auth)
    a, b = do_import(client, auth), do_import(client, auth, visibility="org")
    assert a["name"] != b["name"] and b["visibility"] == "org"


def test_page_limit_truncates_and_reports_it(client, auth, fake, session):
    connect(client, auth)
    imp = do_import(client, auth, SPACE, max_pages=2)
    assert len(docs(session, imp["repo_id"])) == 2
    src = client.get("/api/wiki/sources", headers=auth).json()[0]
    assert src["status"] == "completed" and "limit reached" in src["error"]


def test_incremental_sync_updates_changes_adds_new_and_removes_deleted_pages(client, auth, fake, session):
    connect(client, auth)
    imp = do_import(client, auth)
    sid = client.get("/api/wiki/sources", headers=auth).json()[0]["id"]
    before = {p: d.updated_at for p, d in docs(session, imp["repo_id"]).items()}
    client.post(f"/api/wiki/sources/{sid}/sync", headers=auth)  # nothing changed upstream
    after = docs(session, imp["repo_id"])
    assert {p: d.updated_at for p, d in after.items()} == before and all(d.last_synced_at for d in after.values())
    fake.edit("101", "<h2>Triage</h2><p>Rollback first, then investigate.</p>")
    fake.add("104", "Postmortems", "ENG", ["100"], "<p>Blameless reviews.</p>")
    fake.delete("103")
    client.post(f"/api/wiki/sources/{sid}/sync", headers=auth)
    d = docs(session, imp["repo_id"])
    assert (
        "Rollback first" in d["eng/engineering-handbook/incident-response.md"].content
        and d["eng/engineering-handbook/incident-response.md"].source_version == 2
    )
    assert (
        "eng/engineering-handbook/postmortems.md" in d
        and "eng/engineering-handbook/profile-service/on-call-rotation.md" not in d
    )
    hits = client.post("/api/knowledge/search", headers=auth, json={"query": "Rollback first then investigate"}).json()
    assert hits[0]["file"] == "eng/engineering-handbook/incident-response.md" and hits[0]["url"].startswith(
        "https://fake.atlassian.net/wiki/spaces/ENG/pages/101"
    )
    gone = client.post("/api/knowledge/search", headers=auth, json={"query": "Review rotation quarterly"}).json()
    assert all("on-call" not in h["file"] for h in gone)


def test_renaming_a_page_moves_it_and_keeps_links_resolving(client, auth, fake, session):
    connect(client, auth)
    imp = do_import(client, auth)
    sid = client.get("/api/wiki/sources", headers=auth).json()[0]["id"]
    fake.rename("102", "Profile Service Design")
    client.post(f"/api/wiki/sources/{sid}/sync", headers=auth)
    d = docs(session, imp["repo_id"])
    assert (
        "eng/engineering-handbook/profile-service-design.md" in d
        and "eng/engineering-handbook/profile-service.md" not in d
    )
    assert (
        d["eng/engineering-handbook/profile-service-design.md"].source_id == "102"
    )  # same page, new title/path; not duplicated
    moved = d["eng/engineering-handbook/profile-service-design/on-call-rotation.md"]  # children follow their parent
    assert moved.source_id == "103" and moved.parent_path == "eng/engineering-handbook/profile-service-design.md"
    assert "eng/engineering-handbook/profile-service/on-call-rotation.md" not in d


def test_sync_failures_are_visible_and_do_not_lose_pages(client, auth, fake, session):
    connect(client, auth)
    imp = do_import(client, auth)
    sid = client.get("/api/wiki/sources", headers=auth).json()[0]["id"]
    fake.token = "rotated"  # credentials revoked upstream
    client.post(f"/api/wiki/sources/{sid}/sync", headers=auth)
    s = client.get("/api/wiki/sources", headers=auth).json()[0]
    assert s["status"] == "failed" and "Authentication failed" in s["error"] and TOKEN not in s["error"]
    assert len(docs(session, imp["repo_id"])) == 4  # existing pages are kept


def test_secrets_in_confluence_pages_are_redacted(client, auth, fake, session):
    fake.add(
        "105",
        "Credentials FAQ",
        "ENG",
        ["100"],
        '<p>Use <code>API_TOKEN = "sk-live-abcdefghijklmnopqrstuvwx"</code> for staging.</p>',
    )
    connect(client, auth)
    imp = do_import(client, auth)
    page = docs(session, imp["repo_id"])["eng/engineering-handbook/credentials-faq.md"]
    assert "sk-live-" not in page.content and "[REDACTED]" in page.content


# ------------------------------------------------------------------ access
def test_confluence_imports_are_admin_only_by_default_and_respect_grants(client, auth, fake):
    connect(client, auth)
    imp = do_import(client, auth)
    client.post(
        "/api/auth/users", headers=auth, json={"email": "m@acme.io", "password": "member-pass-1", "role": "member"}
    )
    m = client.post("/api/auth/login", json={"email": "m@acme.io", "password": "member-pass-1"}).json()
    h = {"Authorization": f"Bearer {m['access_token']}"}
    assert client.get("/api/wiki/sources", headers=h).json() == []
    assert client.get("/api/repositories", headers=h).json() == []
    hits = client.post("/api/knowledge/search", headers=h, json={"query": "Incident Response triage deploys"}).json()
    assert "Engineering Handbook" not in str(hits) and "incident-response" not in str(hits)
    assert client.get("/api/graph/stats", headers=h).json()["total_nodes"] == 0
    assert client.post(
        f"/api/wiki/sources/{client.get('/api/wiki/sources', headers=auth).json()[0]['id']}/sync", headers=h
    ).status_code in (403, 404)
    client.put(
        f"/api/repositories/{imp['repo_id']}/access",
        headers=auth,
        json={"visibility": "restricted", "user_ids": [m["user"]["id"]]},
    )
    assert len(client.get("/api/wiki/sources", headers=h).json()) == 1
    assert client.post("/api/knowledge/search", headers=h, json={"query": "Incident Response triage deploys"}).json()


def test_sources_are_tenant_isolated(client, auth, fake):
    connect(client, auth)
    do_import(client, auth)
    sid = client.get("/api/wiki/sources", headers=auth).json()[0]["id"]
    other = register(client, "x@other.io", "Other")
    assert client.get("/api/wiki/sources", headers=other).json() == []
    assert client.post(f"/api/wiki/sources/{sid}/sync", headers=other).status_code in (403, 404)
    assert client.delete(f"/api/wiki/sources/{sid}", headers=other).status_code in (403, 404)


def test_deleting_a_source_can_keep_or_remove_its_pages(client, auth, fake):
    connect(client, auth)
    a, b = do_import(client, auth), do_import(client, auth, name="second")
    sa, sb = [s["id"] for s in client.get("/api/wiki/sources", headers=auth).json()][::-1]
    assert client.delete(f"/api/wiki/sources/{sa}", headers=auth).status_code == 204  # keep pages, stop syncing
    assert client.get(f"/api/repositories/{a['repo_id']}", headers=auth).status_code == 200
    assert client.delete(f"/api/wiki/sources/{sb}", params={"delete_pages": True}, headers=auth).status_code == 204
    assert client.get(f"/api/repositories/{b['repo_id']}", headers=auth).status_code == 404


def test_datacenter_flow(client, auth, monkeypatch):
    dc = FakeConfluence("datacenter", token="pat-xyz")
    monkeypatch.setattr(wi, "confluence_transport", make_transport(dc))
    r = client.put(
        "/api/wiki/confluence/connection",
        headers=auth,
        json={"base_url": "https://fake.corp.io", "deployment": "datacenter", "token": "pat-xyz"},
    )
    assert r.status_code == 200
    imp = client.post(
        "/api/wiki/confluence/import",
        headers=auth,
        json={"url": "https://fake.corp.io/display/ENG/Engineering+Handbook"},
    )
    assert imp.status_code == 202, imp.text
    assert imp.json()["mode"] == "tree"
    assert base64  # (kept to document that Basic auth is cloud-only)
