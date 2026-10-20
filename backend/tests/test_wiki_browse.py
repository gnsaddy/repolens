from datetime import UTC, datetime, timedelta

import pytest

from repolens.models import Document
from tests.conftest import register
from tests.test_api import add_repo
from tests.test_knowledge import make_collection, upload
from tests.test_risk import commit
from tests.test_wiki_confluence import connect, do_import, fake  # noqa: F401  (fixture)

OLD = datetime.now(UTC) - timedelta(days=400)


def collection_id(client, auth, name):
    return next(r["id"] for r in client.get("/api/repositories", headers=auth).json() if r["name"] == name)


@pytest.fixture
def wiki(client, auth, demo_repo, fake):  # noqa: F811
    """A code repo with its own docs, an imported Confluence tree, and an uploaded README collection."""
    commit(
        demo_repo[0],
        {
            "src/export.py": "class Exporter:\n    def run(self):\n        return export_everything()\n\n\ndef export_everything():\n    return []\n"
        },
        "feat: exporter",
        "2026-10-15T10:00:00Z",
    )
    code = add_repo(client, auth, demo_repo[0])
    connect(client, auth)
    imp = do_import(client, auth, visibility="org")
    notes = make_collection(client, auth, "team-notes")
    upload(
        client,
        auth,
        notes["id"],
        {
            "README.md": "# Team notes\n\nSee [the guide](guide.md), [missing](nowhere.md) and [[Profile Service]].\n\n[bad](javascript:alert(1)) [data](data:text/html,x)\n",
            "guide.md": "# Guide\n\nUse `legacy_exporter()` and `OldBillingService.run` before `UserProfileService.get_profile`.\n",
            "orphan.md": "# Lonely page\n\nNobody links here. It talks about `UserProfileService.get_profile` handling.\n",
            "copy-a.md": "# Deploy checklist\n\nRun the migrations, restart the api, verify health endpoints and watch the dashboards for errors.\n",
            "copy-b.md": "# Deploy checklist\n\nRun the migrations, restart the api, verify health endpoints and watch the dashboards for errors.\n",
        },
    )
    return {"code": code["id"], "confluence": imp["repo_id"], "notes": notes["id"]}


# ------------------------------------------------------------------ overview + tree
def test_overview_unifies_repo_docs_uploads_and_confluence(client, auth, wiki):
    o = client.get("/api/wiki/overview", headers=auth).json()
    by = {s["name"]: s for s in o["sources"]}
    assert (
        by["user-service"]["kind"] == "repo"
        and by["user-service"]["origin"] == "repo"
        and by["user-service"]["pages"] >= 2
    )
    assert (
        by["Confluence: Engineering Handbook"]["origin"] == "confluence"
        and by["Confluence: Engineering Handbook"]["pages"] == 4
    )
    assert by["team-notes"]["kind"] == "collection" and by["team-notes"]["pages"] == 5
    assert o["totals"]["pages"] == sum(s["pages"] for s in o["sources"]) and o["concepts"] > 0
    assert {"orphans", "broken_links", "stale", "duplicates", "undocumented_code", "outdated_references"} <= set(
        o["health"]
    )


def test_confluence_tree_follows_the_page_hierarchy(client, auth, wiki):
    t = client.get("/api/wiki/tree", headers=auth, params={"repo_id": wiki["confluence"]}).json()
    assert [n["title"] for n in t] == ["Engineering Handbook"]
    kids = {n["title"]: n for n in t[0]["children"]}
    assert set(kids) == {"Incident Response", "Profile Service"}
    assert [n["title"] for n in kids["Profile Service"]["children"]] == ["On-call Rotation"]
    assert kids["Incident Response"]["path"] == "eng/engineering-handbook/incident-response.md"


def test_repo_and_upload_trees_are_directory_based(client, auth, wiki):
    t = client.get("/api/wiki/tree", headers=auth, params={"repo_id": wiki["code"]}).json()
    flat = str(t)
    assert "README.md" in flat and "docs" in flat and "architecture.md" in flat
    notes = client.get("/api/wiki/tree", headers=auth, params={"repo_id": wiki["notes"]}).json()
    assert {n["key"] for n in notes} == {"README.md", "guide.md", "orphan.md", "copy-a.md", "copy-b.md"}


# ------------------------------------------------------------------ pages
def get_page(client, h, repo, path):
    return client.get("/api/wiki/page", headers=h, params={"repo_id": repo, "path": path})


def test_page_resolves_links_by_title_and_across_sources(client, auth, wiki):
    p = get_page(client, auth, wiki["confluence"], "eng/engineering-handbook.md").json()
    assert p["title"] == "Engineering Handbook" and p["source"]["type"] == "confluence" and p["source"]["version"] == 1
    assert p["source"]["url"].startswith("https://fake.atlassian.net/wiki/spaces/ENG/pages/100")
    assert not p["markdown"].startswith("---")  # front matter is shown as metadata, not content
    cid = wiki["confluence"]
    assert f"[Incident Response](wiki://{cid}/eng/engineering-handbook/incident-response.md)" in p["markdown"]
    assert (
        f"[profile design](wiki://{cid}/eng/engineering-handbook/profile-service.md)" in p["markdown"]
    )  # custom label kept
    assert p["unresolved_links"] == [] and [o["title"] for o in p["outline"]] == ["Engineering Handbook", "Welcome"]


def test_unresolved_and_unsafe_links_are_neutralised(client, auth, wiki):
    p = get_page(client, auth, wiki["notes"], "README.md").json()
    n = wiki["notes"]
    assert f"[the guide](wiki://{n}/guide.md)" in p["markdown"]  # relative link resolved
    assert (
        f"wiki://{wiki['confluence']}/eng/engineering-handbook/profile-service.md" in p["markdown"]
    )  # [[title]] across collections
    assert "nowhere.md" not in p["markdown"].replace("~~", "") or "[missing]" not in p["markdown"]
    assert {"link": "nowhere.md"} in p["unresolved_links"]
    assert (
        "javascript:" not in p["markdown"] and "data:text" not in p["markdown"] and "bad" in p["markdown"]
    )  # label kept, link removed


def test_broken_wiki_links_are_struck_through_and_reported(client, auth, wiki):
    p = get_page(client, auth, wiki["confluence"], "eng/engineering-handbook/incident-response.md").json()
    assert "~~Retired Runbook~~" in p["markdown"] and {"link": "Retired Runbook"} in p["unresolved_links"]
    assert [b["title"] for b in p["breadcrumbs"]] == ["Confluence: Engineering Handbook", "Engineering Handbook"]


def test_page_connections_backlinks_code_and_related(client, auth, wiki):
    inc = get_page(client, auth, wiki["confluence"], "eng/engineering-handbook/incident-response.md").json()
    back = {b["title"] for b in inc["backlinks"]}
    assert back >= {"Engineering Handbook", "On-call Rotation"}  # who links here, from other pages
    assert any(m["name"] == "get_profile" and m["repo"] == "user-service" for m in inc["mentions_code"])  # wiki -> code
    prof = get_page(client, auth, wiki["confluence"], "eng/engineering-handbook/profile-service.md").json()
    assert {m["qualname"] for m in prof["mentions_code"]} >= {
        "UserProfileService.get_profile",
        "UserRepository.find_by_id",
        "handle_profile_request",
    }
    assert {o["title"] for o in inc["links_out"]} >= {"Profile Service"}
    notes = get_page(client, auth, wiki["notes"], "orphan.md").json()
    assert notes["backlinks"] == [] and any(m["name"] == "get_profile" for m in notes["mentions_code"])
    assert "UserProfileService.get_profile" in " ".join(inc["concepts"] + prof["concepts"])


def test_repo_readme_pages_render_from_git(client, auth, wiki):
    p = get_page(client, auth, wiki["code"], "docs/architecture.md").json()
    assert p["source"]["type"] == "repo" and "Profile lookup" in [o["title"] for o in p["outline"]]
    assert f"wiki://{wiki['code']}/src/api.py" not in p["markdown"]  # links to code files are not wiki pages
    assert any(m["name"] == "get_profile" for m in p["mentions_code"])


def test_missing_pages_and_traversal_attempts(client, auth, wiki):
    for path in ("nope.md", "../../etc/passwd", "src/api.py", "/etc/passwd"):
        assert get_page(client, auth, wiki["code"], path).status_code == 404
    assert client.get("/api/wiki/page", headers=auth, params={"repo_id": "nope", "path": "a.md"}).status_code == 404


# ------------------------------------------------------------------ concepts
def test_concept_index_finds_recurring_code_terms_across_sources(client, auth, wiki):
    cs = client.get("/api/wiki/concepts", headers=auth).json()
    top = {c["term"]: c for c in cs}
    gp = top["UserProfileService.get_profile"]
    assert gp["docs"] >= 4 and gp["in_code"] is True and gp["code_like"] is True  # confluence x2, repo docs, notes
    assert cs == sorted(cs, key=lambda c: (-c["docs"], not c["in_code"], -c["sections"], c["term"].lower()))
    assert all(c["docs"] >= 2 for c in cs)
    assert "legacy_exporter" not in top  # only one doc mentions it -> not a concept at min_docs=2
    one = {c["term"] for c in client.get("/api/wiki/concepts", headers=auth, params={"min_docs": 1}).json()}
    assert "legacy_exporter" in one


def test_concept_detail_explains_where_a_term_is_documented_and_implemented(client, auth, wiki):
    d = client.get("/api/wiki/concepts/detail", headers=auth, params={"term": "UserProfileService.get_profile"}).json()
    assert d["documents"] >= 4 and d["code"] and d["code"][0]["qualname"] == "UserProfileService.get_profile"
    assert {m["repo"] for m in d["mentions"]} >= {"Confluence: Engineering Handbook", "user-service", "team-notes"}
    assert d["summary"] and d["summary_source"]["path"] and d["related_terms"]
    heading = client.get("/api/wiki/concepts/detail", headers=auth, params={"term": "Deploy checklist"}).json()
    assert len(heading["definitions"]) == 2  # two pages define it with the same heading
    assert client.get("/api/wiki/concepts/detail", headers=auth, params={"term": "zzz-unknown-term"}).status_code == 404


# ------------------------------------------------------------------ health
def test_health_report_finds_each_kind_of_problem(client, auth, wiki, session):
    h = client.get("/api/wiki/health", headers=auth).json()
    s = h["summary"]
    assert any(o["path"] == "orphan.md" for o in h["orphans"]) and not any(
        o["path"] == "guide.md" for o in h["orphans"]
    )  # guide is linked from README
    assert not any(o["repo"].startswith("Confluence") for o in h["orphans"])  # every Confluence page hangs off the tree
    assert {"Retired Runbook"} <= {b["link"].replace("wiki:", "") for b in h["broken_links"]} and any(
        b["link"] == "nowhere.md" for b in h["broken_links"]
    )
    dup = h["duplicates"][0]
    assert dup["similarity"] >= 0.85 and {dup["a"]["path"], dup["b"]["path"]} == {"copy-a.md", "copy-b.md"}
    refs = {r["term"] for r in h["outdated_references"]}
    assert {"legacy_exporter", "OldBillingService.run"} <= refs and "UserProfileService.get_profile" not in refs
    undocumented = {n["qualname"] for n in h["undocumented_code"]}
    assert {"Exporter", "export_everything"} <= undocumented and "UserProfileService.get_profile" not in undocumented
    assert "UserRepository" not in undocumented  # the Confluence page names it, so it counts as documented
    assert 0 < s["documented_code_ratio"] < 1 and s["orphans"] >= 1 and s["broken_links"] >= 2 and s["duplicates"] >= 1


def test_stale_pages_come_from_source_dates_and_the_threshold(client, auth, wiki, session):
    doc = session.query(Document).filter_by(repo_id=wiki["confluence"], source_id="103").one()
    doc.source_updated_at = OLD
    session.commit()
    h = client.get("/api/wiki/health", headers=auth, params={"stale_days": 180}).json()
    stale = {x["path"]: x for x in h["stale"]}
    assert (
        "eng/engineering-handbook/profile-service/on-call-rotation.md" in stale
        and stale[next(iter(stale))]["age_days"] >= 390
    )
    assert not any(x["path"] == "eng/engineering-handbook.md" for x in h["stale"])  # fresh pages are not stale
    assert client.get("/api/wiki/health", headers=auth, params={"stale_days": 800}).json()["summary"]["stale"] == 0
    assert client.get("/api/wiki/health", headers=auth, params={"limit": 1}).json()["undocumented_code"].__len__() <= 1
    assert client.get("/api/wiki/health", headers=auth, params={"stale_days": 1}).status_code == 422


# ------------------------------------------------------------------ ask
def test_wiki_ask_answers_from_docs_with_citations_and_code_connections(client, auth, wiki):
    r = client.post(
        "/api/wiki/ask",
        headers=auth,
        json={"query": "What should I do when deleted users get HTTP 500 on the profile endpoint?"},
    ).json()
    assert r["mode"] == "extractive" and r["sources"] and all(s["kind"] == "doc" for s in r["sources"])
    assert any(
        s["repo"] == "Confluence: Engineering Handbook" and s["url"].startswith("https://fake.atlassian.net")
        for s in r["sources"]
    )
    assert any(c["to_type"] == "function" for c in r["connections"])  # the code those passages talk about
    assert "[S1]" in r["answer"]
    assert client.post("/api/wiki/ask", headers=auth, json={"query": "x"}).status_code == 422


# ------------------------------------------------------------------ access control and isolation
def member(client, auth, email="m@acme.io"):
    client.post("/api/auth/users", headers=auth, json={"email": email, "password": "member-pass-1", "role": "member"})
    r = client.post("/api/auth/login", json={"email": email, "password": "member-pass-1"}).json()
    return {"Authorization": f"Bearer {r['access_token']}"}, r["user"]["id"]


def test_restricted_confluence_content_is_invisible_everywhere_in_the_wiki(client, auth, wiki):
    h, uid = member(client, auth)
    client.put(
        f"/api/repositories/{wiki['confluence']}/access",
        headers=auth,
        json={"visibility": "restricted", "user_ids": []},
    )
    cid = wiki["confluence"]
    o = client.get("/api/wiki/overview", headers=h).json()
    assert all(s["origin"] != "confluence" for s in o["sources"])
    assert client.get("/api/wiki/tree", headers=h, params={"repo_id": cid}).status_code == 404
    assert get_page(client, h, cid, "eng/engineering-handbook.md").status_code == 404
    crawl = " ".join(
        client.get(u, headers=h).text
        for u in ("/api/wiki/concepts?min_docs=1", "/api/wiki/health", "/api/wiki/overview", "/api/wiki/sources")
    )
    for needle in (
        "Engineering Handbook",
        "Retired Runbook",
        "On-call Rotation",
        "eng/engineering-handbook",
        "fake.atlassian.net",
    ):
        assert needle not in crawl, needle
    ask = client.post("/api/wiki/ask", headers=h, json={"query": "incident response triage deploys"}).json()
    assert "Engineering Handbook" not in str({k: ask[k] for k in ("sources", "answer", "connections")})
    # the visible docs still work, and the hidden doc no longer inflates concept counts for this viewer
    admin_docs = {c["term"]: c["docs"] for c in client.get("/api/wiki/concepts", headers=auth).json()}[
        "UserProfileService.get_profile"
    ]
    member_docs = {c["term"]: c["docs"] for c in client.get("/api/wiki/concepts", headers=h).json()}[
        "UserProfileService.get_profile"
    ]
    assert member_docs < admin_docs
    client.put(f"/api/repositories/{cid}/access", headers=auth, json={"visibility": "restricted", "user_ids": [uid]})
    assert get_page(client, h, cid, "eng/engineering-handbook.md").status_code == 200  # a grant reveals it


def test_a_concept_only_mentioned_in_hidden_docs_does_not_exist_for_others(client, auth, wiki):
    h, _ = member(client, auth)
    client.put(
        f"/api/repositories/{wiki['notes']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []}
    )
    assert client.get("/api/wiki/concepts/detail", headers=h, params={"term": "legacy_exporter"}).status_code == 404
    assert client.get("/api/wiki/concepts/detail", headers=auth, params={"term": "legacy_exporter"}).status_code == 200


def test_wiki_is_tenant_isolated(client, auth, wiki):
    other = register(client, "x@other.io", "Other")
    o = client.get("/api/wiki/overview", headers=other).json()
    assert (
        o["sources"] == []
        and o["totals"]["pages"] == 0
        and client.get("/api/wiki/concepts", headers=other).json() == []
    )
    assert get_page(client, other, wiki["confluence"], "eng/engineering-handbook.md").status_code == 404
    assert client.get("/api/wiki/health", headers=other).json()["summary"]["pages"] == 0


def test_empty_wiki_is_well_formed(client, auth):
    assert client.get("/api/wiki/overview", headers=auth).json()["sources"] == []
    h = client.get("/api/wiki/health", headers=auth).json()
    assert h["summary"]["documented_code_ratio"] is None and h["orphans"] == []
    assert client.post("/api/wiki/ask", headers=auth, json={"query": "anything at all"}).json()["mode"] == "none"
