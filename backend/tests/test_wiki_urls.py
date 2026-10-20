import httpx
import pytest

from repolens.config import get_settings
from repolens.models import Document
from repolens.services import wiki_import as wi
from tests.test_knowledge import make_collection


def serve(monkeypatch, responses: dict[str, httpx.Response | tuple]):
    calls = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(str(r.url))
        hit = responses.get(str(r.url))
        if hit is None:
            return httpx.Response(404)
        return hit if isinstance(hit, httpx.Response) else httpx.Response(*hit)

    monkeypatch.setattr(wi, "url_transport", httpx.MockTransport(handler))
    return calls


README = "# Widget\n\nRun `make run`. See [design](docs/design.md).\n"


def test_plain_markdown_readme_is_imported_with_its_source(client, auth, monkeypatch, session):
    serve(
        monkeypatch,
        {
            "https://example.org/acme/widget/README.md": httpx.Response(
                200, text=README, headers={"content-type": "text/markdown"}
            )
        },
    )
    r = client.post(
        "/api/wiki/import-url",
        headers=auth,
        json={"url": "https://example.org/acme/widget/README.md", "name": "Web docs"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    doc = session.query(Document).one()
    assert (doc.path, doc.source_type, doc.source_url) == (
        "README.md",
        "url",
        "https://example.org/acme/widget/README.md",
    )
    assert doc.content == README and body["secrets_redacted"] == 0
    hits = client.post("/api/knowledge/search", headers=auth, json={"query": "Widget make run"}).json()
    assert hits[0]["file"] == "README.md" and hits[0]["url"] == "https://example.org/acme/widget/README.md"


def test_github_blob_links_are_fetched_as_raw_files(client, auth, monkeypatch):
    calls = serve(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/widget/main/README.md": httpx.Response(
                200, text=README, headers={"content-type": "text/plain; charset=utf-8"}
            )
        },
    )
    r = client.post(
        "/api/wiki/import-url",
        headers=auth,
        json={"url": "https://github.com/acme/widget/blob/main/README.md", "name": "gh"},
    )
    assert r.status_code == 201 and calls == ["https://raw.githubusercontent.com/acme/widget/main/README.md"]


def test_reimport_updates_the_same_document_and_adds_to_an_existing_collection(client, auth, monkeypatch, session):
    col = make_collection(client, auth, "notes")
    url = "https://example.org/notes.txt"
    serve(
        monkeypatch,
        {url: httpx.Response(200, text="first version about zebras", headers={"content-type": "text/plain"})},
    )
    client.post("/api/wiki/import-url", headers=auth, json={"url": url, "collection_id": col["id"]})
    serve(
        monkeypatch,
        {url: httpx.Response(200, text="second version about giraffes", headers={"content-type": "text/plain"})},
    )
    client.post("/api/wiki/import-url", headers=auth, json={"url": url, "collection_id": col["id"]})
    session.expire_all()
    doc = session.query(Document).one()
    assert doc.content == "second version about giraffes" and doc.path == "notes.txt"
    assert client.post("/api/knowledge/search", headers=auth, json={"query": "zebras"}).json() == [] or all(
        "zebras" not in h["content"]
        for h in client.post("/api/knowledge/search", headers=auth, json={"query": "zebras"}).json()
    )


def test_html_pages_become_markdown_without_chrome_or_scripts(client, auth, monkeypatch, session):
    page = "<html><head><title>Runbook</title><script>steal()</script></head><body><nav>menu</nav><h2>Restart</h2><p>Use <code>systemctl restart api</code>.</p><footer>copyright</footer></body></html>"
    serve(
        monkeypatch,
        {
            "https://example.org/runbook": httpx.Response(
                200, text=page, headers={"content-type": "text/html; charset=utf-8"}
            )
        },
    )
    r = client.post("/api/wiki/import-url", headers=auth, json={"url": "https://example.org/runbook", "name": "web"})
    assert r.status_code == 201
    doc = session.query(Document).one()
    assert doc.path == "runbook.md" and "## Restart" in doc.content and "`systemctl restart api`" in doc.content
    assert (
        "steal" not in doc.content
        and "menu" not in doc.content
        and "copyright" not in doc.content
        and doc.content.startswith("# Runbook")
    )


def test_secrets_in_imported_text_are_redacted(client, auth, monkeypatch):
    serve(
        monkeypatch,
        {
            "https://example.org/a.md": httpx.Response(
                200,
                text='# A\n\nexport API_KEY = "sk-live-abcdefghijklmnopqrstuvwx"\n',
                headers={"content-type": "text/markdown"},
            )
        },
    )
    r = client.post("/api/wiki/import-url", headers=auth, json={"url": "https://example.org/a.md", "name": "x"}).json()
    assert r["secrets_redacted"] == 1


@pytest.mark.parametrize(
    "resp,needle",
    [
        (httpx.Response(404), "404"),
        (httpx.Response(200, content=b"\x00\x01\x02binary", headers={"content-type": "text/plain"}), "binary"),
        (httpx.Response(200, content=b"x" * 1_100_000, headers={"content-type": "text/plain"}), "1 MB"),
        (httpx.Response(200, text="{}", headers={"content-type": "application/pdf"}), "content type"),
    ],
)
def test_bad_responses_are_rejected_clearly(client, auth, monkeypatch, resp, needle):
    serve(monkeypatch, {"https://example.org/f": resp})
    r = client.post("/api/wiki/import-url", headers=auth, json={"url": "https://example.org/f", "name": "x"})
    assert r.status_code == 400 and needle in r.text


def test_validation(client, auth):
    assert (
        client.post("/api/wiki/import-url", headers=auth, json={"url": "https://example.org/a.md"}).status_code == 422
    )  # no collection and no name
    assert (
        client.post(
            "/api/wiki/import-url", headers=auth, json={"url": "https://u:p@example.org/a.md", "name": "x"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/wiki/import-url", headers=auth, json={"url": "ftp://example.org/a.md", "name": "x"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/wiki/import-url", headers=auth, json={"url": "https://example.org/a.md", "collection_id": "nope"}
        ).status_code
        == 404
    )


# ------------------------------------------------------------------ SSRF protection (production settings)
@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setenv("REPOLENS_ENV", "prod")
    get_settings.cache_clear()
    monkeypatch.setattr(
        wi, "_public_host", lambda h: h not in ("internal.corp", "127.0.0.1", "169.254.169.254", "localhost")
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/a.md",
        "https://internal.corp/a.md",
        "https://127.0.0.1/a.md",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/x",
    ],
)
def test_private_and_insecure_targets_are_blocked_in_production(client, auth, prod, monkeypatch, url):
    calls = serve(monkeypatch, {})
    r = client.post("/api/wiki/import-url", headers=auth, json={"url": url, "name": "x"})
    assert r.status_code == 400 and not calls  # nothing was even requested


def test_redirects_to_private_hosts_are_blocked_and_redirect_loops_end(client, auth, prod, monkeypatch):
    serve(
        monkeypatch,
        {"https://example.org/go": httpx.Response(302, headers={"location": "https://internal.corp/secret"})},
    )
    r = client.post("/api/wiki/import-url", headers=auth, json={"url": "https://example.org/go", "name": "x"})
    assert r.status_code == 400 and "public host" in r.text
    serve(
        monkeypatch, {"https://example.org/loop": httpx.Response(302, headers={"location": "https://example.org/loop"})}
    )
    r = client.post("/api/wiki/import-url", headers=auth, json={"url": "https://example.org/loop", "name": "x"})
    assert r.status_code == 400 and "redirects" in r.text


def test_safe_redirects_are_followed(client, auth, prod, monkeypatch):
    serve(
        monkeypatch,
        {
            "https://example.org/old": httpx.Response(301, headers={"location": "/new.md"}),
            "https://example.org/new.md": httpx.Response(
                200, text="# New\n", headers={"content-type": "text/markdown"}
            ),
        },
    )
    r = client.post("/api/wiki/import-url", headers=auth, json={"url": "https://example.org/old", "name": "x"})
    assert r.status_code == 201 and r.json()["source_url"] == "https://example.org/new.md"


def test_viewers_cannot_import_urls(client, auth, monkeypatch):
    client.post(
        "/api/auth/users", headers=auth, json={"email": "v@acme.io", "password": "viewer-pass-1", "role": "viewer"}
    )
    tok = client.post("/api/auth/login", json={"email": "v@acme.io", "password": "viewer-pass-1"}).json()[
        "access_token"
    ]
    calls = serve(monkeypatch, {})
    r = client.post(
        "/api/wiki/import-url",
        headers={"Authorization": f"Bearer {tok}"},
        json={"url": "https://example.org/a.md", "name": "x"},
    )
    assert r.status_code == 403 and not calls
