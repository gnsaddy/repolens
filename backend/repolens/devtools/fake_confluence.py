"""A tiny in-memory Confluence REST API (subset) for tests and local demos. Not for production use.

python -m repolens.devtools.fake_confluence --port 8099 --token demo-token --email demo@example.com
"""

import base64
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, quote_plus, urlparse

CLOUD_BASE = "https://fake.atlassian.net/wiki"

SAMPLE = [
    (
        "100",
        "Engineering Handbook",
        "ENG",
        [],
        '<h1>Welcome</h1><p>Start with <ac:link><ri:page ri:content-title="Incident Response" /></ac:link> '
        'and the <ac:link><ri:page ri:content-title="Profile Service" /><ac:plain-text-link-body><![CDATA[profile design]]></ac:plain-text-link-body></ac:link>.</p>'
        '<p>New joiners should read <a href="https://example.com/onboarding">the onboarding guide</a>.</p>',
    ),
    (
        "101",
        "Incident Response",
        "ENG",
        ["100"],
        "<h2>Triage</h2><p>Check <strong>recent deploys</strong> first. A common cause of HTTP 500 on the profile endpoint is a missing null check in "
        "<code>UserProfileService.get_profile</code>.</p>"
        '<ac:structured-macro ac:name="warning"><ac:rich-text-body><p>Never restart the gateway during peak hours.</p></ac:rich-text-body></ac:structured-macro>'
        '<h2>Deleted users</h2><p>Deleted users must still get a default profile. See <ac:link><ri:page ri:content-title="Profile Service" /></ac:link> '
        'and <ac:link><ri:page ri:content-title="Retired Runbook" /></ac:link>.</p>'
        '<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">bash</ac:parameter><ac:plain-text-body><![CDATA[kubectl rollout undo deploy/api]]></ac:plain-text-body></ac:structured-macro>',
    ),
    (
        "102",
        "Profile Service",
        "ENG",
        ["100"],
        "<h2>Data flow</h2><p><code>handle_profile_request</code> calls <code>UserProfileService.get_profile</code>, which reads "
        "<code>UserRepository.find_by_id</code>.</p><table><tbody><tr><th>Component</th><th>Owner</th></tr><tr><td>api</td><td>team-platform</td></tr></tbody></table>"
        "<ul><li>Missing users return a default profile</li><li>Metadata may be null</li></ul>",
    ),
    (
        "103",
        "On-call Rotation",
        "ENG",
        ["100", "102"],
        '<p>The rotation lives in the paging tool. Escalate to the <ac:link><ri:page ri:content-title="Incident Response" /></ac:link> guide.</p>'
        "<ac:task-list><ac:task><ac:task-status>incomplete</ac:task-status><ac:task-body>Review rotation quarterly</ac:task-body></ac:task></ac:task-list>",
    ),
    ("200", "Finance Policies", "FIN", [], "<h1>Expenses</h1><p>Submit receipts within 30 days.</p>"),
]


def _page_json(p: dict, base: str, titles: dict[str, str] | None = None) -> dict:
    titles = titles or {}
    return {
        "id": p["id"],
        "type": "page",
        "title": p["title"],
        "space": {"key": p["space"], "name": p["space"] + " space"},
        "version": {"number": p["version"], "when": p["updated"]},
        "ancestors": [{"id": a, "title": titles.get(a, "?")} for a in p["ancestors"]],
        "body": {"storage": {"value": p["body"], "representation": "storage"}},
        "history": {"lastUpdated": {"when": p["updated"]}},
        "_links": {"webui": f"/spaces/{p['space']}/pages/{p['id']}/{quote_plus(p['title'])}", "base": base},
    }


class FakeConfluence:
    def __init__(self, deployment: str = "cloud", email: str = "bot@acme.io", token: str = "good-token") -> None:
        self.deployment, self.email, self.token = deployment, email, token
        self.pages: dict[str, dict] = {}
        self.requests: list[tuple[str, str]] = []
        self.fail_next: list[int] = []
        self.denied_spaces: set[str] = set()
        self.comments: list[dict] = []
        self.deny_comments = False
        for pid, title, space, anc, body in SAMPLE:
            self.add(pid, title, space, anc, body)

    @property
    def base(self) -> str:
        return CLOUD_BASE if self.deployment == "cloud" else "https://fake.corp.io"

    def add(self, pid: str, title: str, space: str, ancestors: list[str], body: str, version: int = 1) -> None:
        self.pages[pid] = {
            "id": pid,
            "title": title,
            "space": space,
            "ancestors": ancestors,
            "body": body,
            "version": version,
            "updated": datetime.now(UTC).isoformat(),
        }

    def edit(self, pid: str, body: str) -> None:
        p = self.pages[pid]
        p["body"], p["version"], p["updated"] = body, p["version"] + 1, datetime.now(UTC).isoformat()

    def rename(self, pid: str, title: str) -> None:
        p = self.pages[pid]
        p["title"], p["version"] = title, p["version"] + 1

    def delete(self, pid: str) -> None:
        self.pages.pop(pid, None)

    def _post(self, rest: str, raw: bytes) -> tuple[int, dict | str, dict[str, str]]:
        import json

        if rest != "/content":
            return 404, {"message": "no route"}, {}
        if self.deny_comments:
            return 403, {"message": "forbidden"}, {}
        data = json.loads(raw or b"{}")
        if data.get("type") != "comment" or (data.get("container") or {}).get("id") not in self.pages:
            return 404, {"message": "no such page"}, {}
        cid = str(900 + len(self.comments))
        self.comments.append({"id": cid, "page": data["container"]["id"], "body": data["body"]["storage"]["value"]})
        return 200, {"id": cid, "type": "comment"}, {}

    # -- HTTP ------------------------------------------------------------------------------------------------------
    def _authorised(self, headers: dict[str, str]) -> bool:
        auth = headers.get("authorization", "")
        if self.deployment == "cloud":
            return auth == "Basic " + base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        return auth == f"Bearer {self.token}"

    def handle(
        self, method: str, url: str, headers: dict[str, str], body: bytes = b""
    ) -> tuple[int, dict | str, dict[str, str]]:
        u = urlparse(url)
        path = u.path
        self.requests.append((method, path + ("?" + u.query if u.query else "")))
        prefix = "/wiki/rest/api" if self.deployment == "cloud" else "/rest/api"
        if method not in ("GET", "POST") or not path.startswith(prefix):
            return 404, "not found", {}
        if not self._authorised({k.lower(): v for k, v in headers.items()}):
            return 401, {"message": "Unauthorized"}, {}
        if method == "POST":
            return self._post(path[len(prefix) :], body)
        if self.fail_next:
            code = self.fail_next.pop(0)
            return code, {"message": "injected"}, {"Retry-After": "0"} if code == 429 else {}
        rest = path[len(prefix) :]
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        limit, start = int(q.get("limit", 25)), int(q.get("start", 0))
        if rest == "/user/current":
            return 200, {"displayName": "RepoLens Bot", "accountId": "abc"}, {}
        if rest == "/space":
            keys = sorted({p["space"] for p in self.pages.values() if p["space"] not in self.denied_spaces})
            return (
                200,
                {
                    "results": [{"key": k, "name": f"{k} space", "type": "global"} for k in keys],
                    "start": 0,
                    "limit": limit,
                },
                {},
            )
        if rest.startswith("/space/"):
            key = rest.split("/")[2]
            if key in self.denied_spaces:
                return 403, {"message": "forbidden"}, {}
            if not any(p["space"] == key for p in self.pages.values()):
                return 404, {"message": "no space"}, {}
            return 200, {"key": key, "name": f"{key} space"}, {}
        if rest == "/content":
            items = [
                p
                for p in self.pages.values()
                if p["space"] == q.get("spaceKey") and p["space"] not in self.denied_spaces
            ]
            if q.get("title"):
                items = [p for p in items if p["title"] == q["title"]]
            return 200, self._page_list(items, limit, start), {}
        parts = rest.strip("/").split("/")
        if parts[0] == "content" and len(parts) >= 2:
            page = self.pages.get(parts[1])
            if page is None or page["space"] in self.denied_spaces:
                return (403 if page else 404), {"message": "no page"}, {}
            if len(parts) == 2:
                return 200, _page_json(page, self.base, self._titles()), {}
            if parts[2:] == ["child", "page"]:
                kids = [p for p in self.pages.values() if p["ancestors"] and p["ancestors"][-1] == page["id"]]
                return 200, self._page_list(kids, limit, start), {}
        return 404, {"message": "unknown endpoint"}, {}

    def _titles(self) -> dict[str, str]:
        return {pid: p["title"] for pid, p in self.pages.items()}

    def _page_list(self, items: list[dict], limit: int, start: int) -> dict:
        window = items[start : start + limit]
        out: dict = {
            "results": [_page_json(p, self.base, self._titles()) for p in window],
            "start": start,
            "limit": limit,
        }
        if start + limit < len(items):
            out["_links"] = {"next": f"?start={start + limit}"}
        return out


def serve(port: int, fake: FakeConfluence) -> None:  # pragma: no cover - manual demo server
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def _serve(self, method: str) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            code, body, extra = fake.handle(method, self.path, dict(self.headers), self.rfile.read(n) if n else b"")
            raw = (json.dumps(body) if not isinstance(body, str) else body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for k, v in extra.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

        def log_message(self, *a: object) -> None:
            pass

    print(f"fake Confluence ({fake.deployment}) on http://127.0.0.1:{port}  user={fake.email} token={fake.token}")
    HTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--email", default="demo@example.com")
    ap.add_argument("--token", default="demo-token")
    ap.add_argument("--deployment", default="cloud", choices=["cloud", "datacenter"])
    a = ap.parse_args()
    serve(a.port, FakeConfluence(a.deployment, a.email, a.token))
