from repolens.devtools.fake_confluence import FakeConfluence
from repolens.models import DocProposal, Document
from repolens.services import wiki_import as wi
from repolens.services.confluence import markdown_to_storage
from tests.conftest import register
from tests.test_api import add_repo, run_investigation
from tests.test_drift import drift, reindex
from tests.test_knowledge import make_collection, upload
from tests.test_risk import ORDERS, commit, shop  # noqa: F401  (fixture)
from tests.test_wiki_confluence import make_transport


def drifted(client, auth, path, name="billing.md"):
    repo = add_repo(client, auth, path, name="shop")
    col = make_collection(client, auth, "handbook")
    upload(client, auth, col["id"], {name: "# Billing\n\nTotals come from `compute_total`.\n\n## Tax\nAdded last.\n"})
    commit(
        path,
        {"shop/orders.py": ORDERS.replace("(1 + tax_rate)", "(1 + tax_rate) + 1")},
        "fix: add handling fee to totals",
        "2026-09-01T10:00:00Z",
    )
    reindex(client, auth, repo["id"])
    alert = next(a for a in drift(client, auth) if a["doc_path"] == name)
    return col, alert


def test_drift_to_update_proposal_without_a_model_adds_an_honest_review_note(client, auth, shop, session):  # noqa: F811
    col, alert = drifted(client, auth, shop[0])
    r = client.post(f"/api/wiki/proposals/from-drift/{alert['id']}", headers=auth)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["kind"] == "update" and p["drafted_by"] == "template" and "No AI model is connected" in p["rationale"]
    assert (
        "**Review needed:** `shop.orders.compute_total`" in p["proposed"]
        or "**Review needed:** `compute_total`" in p["proposed"]
    )
    assert "handling fee" in p["proposed"]  # the commit message is quoted as the reason
    assert p["original"] in p["proposed"].replace("\n> ", "\n") or "## Tax" in p["proposed"]
    assert p["diff"].startswith("--- a/billing.md") and "+> **Review needed" in p["diff"]
    # nothing is written before approval
    assert (
        session.query(Document).filter_by(repo_id=col["id"], path="billing.md").one().content.count("Review needed")
        == 0
    )
    assert (
        client.post(f"/api/wiki/proposals/from-drift/{alert['id']}", headers=auth).json()["id"] == p["id"]
    )  # idempotent while pending
    assert [x["id"] for x in client.get("/api/wiki/proposals", headers=auth).json()] == [p["id"]]


def test_edit_then_approve_updates_the_page_and_closes_the_alert(client, auth, shop, session):  # noqa: F811
    col, alert = drifted(client, auth, shop[0])
    p = client.post(f"/api/wiki/proposals/from-drift/{alert['id']}", headers=auth).json()
    edited = p["proposed"].replace("Totals come from", "Totals (including the handling fee) come from")
    assert (
        client.put(f"/api/wiki/proposals/{p['id']}", headers=auth, json={"proposed": edited}).json()["drafted_by"]
        == "edited"
    )
    d = client.post(f"/api/wiki/proposals/{p['id']}/decision", headers=auth, json={"approve": True})
    assert (
        d.status_code == 200 and d.json()["status"] == "applied" and "Updated handbook/billing.md" in d.json()["result"]
    )
    session.expire_all()
    assert (
        "including the handling fee"
        in session.query(Document).filter_by(repo_id=col["id"], path="billing.md").one().content
    )
    assert [
        a for a in drift(client, auth) if a["doc_path"] == "billing.md"
    ] == []  # editing the page resolved its alert
    assert (
        client.post(f"/api/wiki/proposals/{p['id']}/decision", headers=auth, json={"approve": False}).status_code == 409
    )  # decided once


def test_stale_draft_is_refused_and_reject_changes_nothing(client, auth, shop, session):  # noqa: F811
    col, alert = drifted(client, auth, shop[0])
    p = client.post(f"/api/wiki/proposals/from-drift/{alert['id']}", headers=auth).json()
    upload(client, auth, col["id"], {"billing.md": "# Billing\n\nSomeone rewrote this meanwhile.\n"})
    r = client.post(f"/api/wiki/proposals/{p['id']}/decision", headers=auth, json={"approve": True})
    assert r.status_code == 409 and "changed after this draft" in r.json()["detail"]
    assert session.get(DocProposal, p["id"]).status == "failed"

    assert (
        client.post(f"/api/wiki/proposals/{p['id']}/decision", headers=auth, json={"approve": False}).status_code == 409
    )


def test_model_draft_is_used_when_configured_and_implausible_output_is_discarded(  # noqa: F811
    client, auth, shop, session, monkeypatch
):
    from repolens.services import proposals as svc

    class FakeLLM:
        offline = False

        def __init__(self, reply):
            self.reply = reply

        def complete(self, agent, system, prompt, max_tokens=0, **kw):
            assert "handling fee" in prompt and "compute_total" in prompt
            return type("R", (), {"text": self.reply})()

    col, alert = drifted(client, auth, shop[0])
    good = "# Billing\n\nTotals come from `compute_total`, which now adds a handling fee.\n\n## Tax\nAdded last.\n"
    monkeypatch.setattr(svc, "_llm", lambda *a, **k: FakeLLM("```markdown\n" + good + "```"))
    p = client.post(f"/api/wiki/proposals/from-drift/{alert['id']}", headers=auth).json()
    assert p["drafted_by"] == "llm" and "adds a handling fee" in p["proposed"] and "```" not in p["proposed"]
    client.post(f"/api/wiki/proposals/{p['id']}/decision", headers=auth, json={"approve": False})
    monkeypatch.setattr(svc, "_llm", lambda *a, **k: FakeLLM("x"))
    p2 = client.post(f"/api/wiki/proposals/from-drift/{alert['id']}", headers=auth).json()
    assert p2["drafted_by"] == "template"  # a 1-character "rewrite" of a whole page is not trusted


def test_confluence_pages_receive_a_comment_never_an_edit(client, auth, monkeypatch, session):
    fake = FakeConfluence()
    monkeypatch.setattr(wi, "confluence_transport", make_transport(fake))
    assert (
        client.put(
            "/api/wiki/confluence/connection",
            headers=auth,
            json={
                "base_url": "https://acme.atlassian.net/wiki",
                "deployment": "cloud",
                "email": "bot@acme.io",
                "token": "good-token",
            },
        ).status_code
        == 200
    )
    r = client.post(
        "/api/wiki/confluence/import",
        headers=auth,
        json={"url": "https://acme.atlassian.net/wiki/spaces/ENG/pages/101", "mode": "page"},
    )
    assert r.status_code == 202, r.text
    doc = session.query(Document).filter_by(repo_id=r.json()["repo_id"], source_type="confluence").one()
    p = DocProposal(
        org_id=doc.org_id,
        kind="update",
        source="drift",
        doc_repo_id=doc.repo_id,
        doc_path=doc.path,
        title="Incident",
        original=doc.content,
        proposed=doc.content + "\nNew step\n",
        rationale="code changed",
        base_hash=doc.content_hash,
    )
    session.add(p)
    session.commit()
    local = client.post(f"/api/wiki/proposals/{p.id}/decision", headers=auth, json={"approve": True, "target": "local"})
    assert local.status_code == 409 and "only receive a comment" in local.json()["detail"]
    p2 = DocProposal(
        org_id=doc.org_id,
        kind="update",
        source="drift",
        doc_repo_id=doc.repo_id,
        doc_path=doc.path,
        title="Incident",
        original=doc.content,
        proposed=doc.content + "\nNew step\n",
        rationale="code changed",
        base_hash=doc.content_hash,
    )
    session.add(p2)
    session.commit()
    ok = client.post(
        f"/api/wiki/proposals/{p2.id}/decision", headers=auth, json={"approve": True, "target": "confluence_comment"}
    )
    assert ok.status_code == 200 and "Posted a comment" in ok.json()["result"]
    assert (
        len(fake.comments) == 1
        and fake.comments[0]["page"] == doc.source_id
        and "RepoLens suggests an update" in fake.comments[0]["body"]
    )
    assert (
        "+New step" in fake.comments[0]["body"] and fake.pages[doc.source_id]["version"] == 1
    )  # the page itself is untouched
    fake.deny_comments = True
    p3 = DocProposal(
        org_id=doc.org_id,
        kind="update",
        source="drift",
        doc_repo_id=doc.repo_id,
        doc_path=doc.path,
        title="Incident",
        original=doc.content,
        proposed=doc.content + "x",
        rationale="r",
        base_hash=doc.content_hash,
    )
    session.add(p3)
    session.commit()
    denied = client.post(
        f"/api/wiki/proposals/{p3.id}/decision", headers=auth, json={"approve": True, "target": "confluence_comment"}
    )
    assert denied.status_code == 409 and "not allowed to comment" in denied.json()["detail"]


def test_confirmed_incident_becomes_a_runbook_proposal(client, auth, demo_repo, session):
    repo = add_repo(client, auth, demo_repo[0])
    inv = run_investigation(client, auth, repo["id"])
    r = client.post("/api/wiki/proposals/from-incident", headers=auth, json={"investigation_id": inv["id"]})
    assert r.status_code == 409 and "Confirm the root cause" in r.json()["detail"]
    client.post(f"/api/investigations/{inv['id']}/review", headers=auth, json={"outcome": "confirmed"})
    r = client.post("/api/wiki/proposals/from-incident", headers=auth, json={"investigation_id": inv["id"]})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["kind"] == "new" and p["doc_path"].startswith("runbooks/") and p["drafted_by"] == "template"
    for section in ("## Symptoms", "## Diagnosis", "## Fix", "## Verification", "## Prevention", "## References"):
        assert section in p["proposed"]
    assert "user_profile.py" in p["proposed"] and f"investigation: {inv['id']}" in p["proposed"]
    d = client.post(f"/api/wiki/proposals/{p['id']}/decision", headers=auth, json={"approve": True})
    assert d.status_code == 200 and d.json()["result"].startswith("Created Runbooks/runbooks/")
    docs = session.query(Document).all()
    assert any(x.path.startswith("runbooks/") and "Symptoms" in x.content for x in docs)
    page = client.get("/api/wiki/overview", headers=auth).json()
    assert any(s["name"] == "Runbooks" for s in page["sources"])


def test_access_control_and_markdown_to_storage(client, auth, shop, session):  # noqa: F811
    col, alert = drifted(client, auth, shop[0])
    p = client.post(f"/api/wiki/proposals/from-drift/{alert['id']}", headers=auth).json()
    other = register(client, "x@other.io", "Other")
    for call in (
        lambda: client.get(f"/api/wiki/proposals/{p['id']}", headers=other),
        lambda: client.post(f"/api/wiki/proposals/{p['id']}/decision", headers=other, json={"approve": True}),
        lambda: client.put(f"/api/wiki/proposals/{p['id']}", headers=other, json={"proposed": "x"}),
    ):
        assert call().status_code == 404
    assert client.get("/api/wiki/proposals", headers=other).json() == []
    assert client.post(f"/api/wiki/proposals/from-drift/{alert['id']}", headers=other).status_code == 404
    html = markdown_to_storage(
        "# T\n\nSome `code` and **bold** <script>x</script> [l](https://a.io) [bad](javascript:alert(1))\n\n- one\n- two\n\n> quote\n\n```diff\n-a\n+b\n```\n"
    )
    assert (
        "<h1>T</h1>" in html
        and "<code>code</code>" in html
        and "<strong>bold</strong>" in html
        and "<ul><li>one</li><li>two</li></ul>" in html
    )
    assert (
        "<script>" not in html
        and "&lt;script&gt;" in html
        and 'href="https://a.io"' in html
        and "javascript:" not in html.replace("[bad](javascript:alert(1))", "")
    )
    assert "<blockquote>" in html and "CDATA[-a\n+b]]" in html


def test_proposal_detail_always_lists_where_it_can_be_applied(client, auth, demo_repo):
    """The review page needs apply_options; without it the page failed to render (blank screen)."""
    repo = add_repo(client, auth, demo_repo[0])
    inv = run_investigation(client, auth, repo["id"])
    client.post(f"/api/investigations/{inv['id']}/review", headers=auth, json={"outcome": "confirmed"})
    p = client.post("/api/wiki/proposals/from-incident", headers=auth, json={"investigation_id": inv["id"]})
    assert p.status_code == 201 and p.json()["apply_options"] == ["local"]
    assert client.get(f"/api/wiki/proposals/{p.json()['id']}", headers=auth).json()["apply_options"] == ["local"]
