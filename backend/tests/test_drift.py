import pytest

from repolens.models import DriftAlert
from repolens.services import drift as drift_svc
from tests.conftest import register
from tests.test_api import add_repo
from tests.test_chat import make_user
from tests.test_knowledge import make_collection, upload
from tests.test_risk import ORDERS, commit, shop  # noqa: F401  (fixture)


def reindex(client, auth, rid):
    assert client.post(f"/api/repositories/{rid}/index", headers=auth).status_code == 202


def drift(client, auth, status="open"):
    return client.get("/api/wiki/drift", headers=auth, params={"status": status}).json()


def test_changed_code_flags_the_pages_that_describe_it(client, auth, shop, session):  # noqa: F811
    path, _ = shop
    repo = add_repo(client, auth, path, name="shop")
    col = make_collection(client, auth, "handbook")
    upload(
        client,
        auth,
        col["id"],
        {
            "billing.md": "# Billing\nTotals come from `compute_total`.",
            "unrelated.md": "# Parties\nNothing about code.",
        },
    )
    assert drift(client, auth) == []  # first index: nothing to compare against

    new = ORDERS.replace("round(subtotal * (1 + tax_rate), 2)", "round(subtotal * (1 + tax_rate) + 1, 2)")
    commit(path, {"shop/orders.py": new}, "change total", "2026-09-01T10:00:00Z")
    reindex(client, auth, repo["id"])
    alerts = drift(client, auth)
    pages = {(a["doc_repo"], a["doc_path"]) for a in alerts}
    assert ("handbook", "billing.md") in pages and ("handbook", "unrelated.md") not in pages
    # the README-style page inside the repository is flagged too: docs/pricing.md mentions compute_total
    assert ("shop", "docs/pricing.md") in pages
    a = next(x for x in alerts if x["doc_path"] == "billing.md")
    assert (
        [s["name"] for s in a["symbols"]] == ["compute_total"] and a["repo"] == "shop" and a["from_sha"] != a["to_sha"]
    )
    assert client.get("/api/wiki/drift/count", headers=auth).json() == {"open": len(alerts)}

    # a second change to the same function accumulates on the same alert instead of duplicating it
    commit(path, {"shop/orders.py": new.replace("+ 1, 2", "+ 2, 2")}, "change again", "2026-09-02T10:00:00Z")
    reindex(client, auth, repo["id"])
    again = [x for x in drift(client, auth) if x["doc_path"] == "billing.md"]
    assert len(again) == 1 and again[0]["to_sha"] != a["to_sha"] and again[0]["from_sha"] == a["from_sha"]


def test_editing_the_page_resolves_the_alert_and_docs_changed_together_are_not_flagged(client, auth, shop, session):  # noqa: F811
    path, _ = shop
    repo = add_repo(client, auth, path, name="shop")
    col = make_collection(client, auth, "handbook")
    upload(client, auth, col["id"], {"billing.md": "# Billing\n`compute_total` adds tax."})
    # code and its in-repo doc change in the same commit -> the doc was kept up to date
    new = ORDERS.replace("round(subtotal * (1 + tax_rate), 2)", "round(subtotal * (1 + tax_rate) + 1, 2)")
    commit(
        path,
        {
            "shop/orders.py": new,
            "docs/pricing.md": "# Pricing\n\nThe total comes from `compute_total`, now plus a fee.\n",
        },
        "fee",
        "2026-09-01T10:00:00Z",
    )
    reindex(client, auth, repo["id"])
    pages = {(a["doc_repo"], a["doc_path"]) for a in drift(client, auth)}
    assert pages == {("handbook", "billing.md")}

    upload(
        client, auth, col["id"], {"billing.md": "# Billing\n`compute_total` adds tax and a fee."}
    )  # someone updates the page
    assert drift(client, auth) == []
    assert [a["status"] for a in drift(client, auth, "resolved")] == ["resolved"]


def test_dismiss_and_access(client, auth, shop, session):  # noqa: F811
    path, _ = shop
    repo = add_repo(client, auth, path, name="shop")
    col = make_collection(client, auth, "handbook")
    upload(client, auth, col["id"], {"billing.md": "# Billing\n`compute_total` adds tax."})
    commit(
        path,
        {"shop/orders.py": ORDERS.replace("(1 + tax_rate)", "(1 + tax_rate * 1)")},
        "tweak",
        "2026-09-01T10:00:00Z",
    )
    reindex(client, auth, repo["id"])
    a = next(x for x in drift(client, auth) if x["doc_path"] == "billing.md")
    other = register(client, "x@other.io", "Other")
    assert client.post(f"/api/wiki/drift/{a['id']}/dismiss", headers=other).status_code == 404
    assert drift(client, other) == []
    assert client.post(f"/api/wiki/drift/{a['id']}/dismiss", headers=auth).json() == {"status": "dismissed"}
    assert all(x["id"] != a["id"] for x in drift(client, auth))
    assert [x["id"] for x in drift(client, auth, "dismissed")] == [a["id"]]
    session.expire_all()
    assert session.get(DriftAlert, a["id"]).resolved_by


def test_unreachable_previous_revision_is_ignored(client, auth, shop, session):  # noqa: F811
    from repolens.models import Repository
    from repolens.services import drift as d

    path, _ = shop
    repo = add_repo(client, auth, path, name="shop")
    r = session.get(Repository, repo["id"])
    assert d.detect(session, r, str(path), "deadbeef" * 5, "cafebabe" * 5) == 0
    assert d.detect(session, r, str(path), "", "abc") == 0


@pytest.mark.parametrize(
    ("name", "qualname", "expected"),
    [
        ("compute_total", "compute_total", True),
        ("graph_overview", "graph_overview", True),
        ("makeInvoice", "makeInvoice", True),
        ("Repository", "Repository", True),
        ("add_item", "Orders.add_item", True),
        ("text", "_Resolver.text", True),  # the class is a specific name, so a page naming the method means it
        ("repo", "repo", False),
        ("ask", "ask", False),
        ("execute", "execute", False),
        ("snapshot", "snapshot", False),
        ("run", "Job.run", False),
    ],
)
def test_a_plain_word_is_not_evidence_that_a_page_describes_a_function(name, qualname, expected):
    assert drift_svc.specific(name, qualname) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("CHANGELOG.md", True),
        ("changelog/v0.1-beta.md", True),
        ("docs/release-notes.md", True),
        ("docs/RELEASE_NOTES.md", True),
        ("docs/archive/old-design.md", True),
        ("history.md", True),
        ("docs/architecture.md", False),
        ("docs/features.md", False),
        ("docs/changes-to-the-api.md", False),
        ("README.md", False),
    ],
)
def test_pages_that_record_history_are_told_from_pages_that_describe_the_code(path, expected):
    assert drift_svc.is_history(path) is expected


def test_a_changelog_is_not_flagged_when_the_code_it_names_changes(client, auth, shop, session):  # noqa: F811
    path, _ = shop
    repo = add_repo(client, auth, path, name="shop")
    col = make_collection(client, auth, "handbook")
    upload(
        client,
        auth,
        col["id"],
        {
            "billing.md": "# Billing\nTotals come from `compute_total`.",
            "CHANGELOG.md": "# Changes\n- `compute_total` now adds tax.",
            "docs/release-notes.md": "# Notes\n`compute_total` was added.",
        },
    )
    new = ORDERS.replace("round(subtotal * (1 + tax_rate), 2)", "round(subtotal * (1 + tax_rate) + 1, 2)")
    commit(path, {"shop/orders.py": new}, "change total", "2026-09-01T10:00:00Z")
    reindex(client, auth, repo["id"])
    pages = {a["doc_path"] for a in drift(client, auth) if a["doc_repo"] == "handbook"}
    assert pages == {"billing.md"}


def test_alerts_that_should_never_have_been_raised_are_dropped_and_weak_names_trimmed(client, auth, shop, session):  # noqa: F811
    path, _ = shop
    repo = add_repo(client, auth, path, name="shop")
    org = session.query(DriftAlert).count() and None  # no alerts yet
    from repolens.models import Repository

    org = session.get(Repository, repo["id"]).org_id

    def alert(doc, names):
        syms = [{"id": n, "name": n.split(".")[-1], "qualname": n, "path": "a.py", "type": "function"} for n in names]
        session.add(DriftAlert(org_id=org, repo_id=repo["id"], doc_repo_id=repo["id"], doc_path=doc, symbols=syms))

    alert("changelog/v0.1-beta.md", ["compute_total"])
    alert("docs/a.md", ["repo", "ask"])
    alert("docs/b.md", ["repo", "compute_total"])
    alert("docs/c.md", ["compute_total"])
    session.commit()
    assert drift_svc.prune(session, org) == 2
    session.commit()
    left = {a.doc_path: [s["qualname"] for s in a.symbols] for a in session.query(DriftAlert)}
    assert left == {"docs/b.md": ["compute_total"], "docs/c.md": ["compute_total"]}


def test_an_alert_says_why_it_was_raised_the_line_that_mentions_the_code_and_what_changed(client, auth, shop, session):  # noqa: F811
    path, _ = shop
    repo = add_repo(client, auth, path, name="shop")
    col = make_collection(client, auth, "handbook")
    upload(
        client, auth, col["id"], {"billing.md": "# Billing\n\nSome words.\nTotals come from `compute_total` and tax.\n"}
    )
    new = ORDERS.replace("round(subtotal * (1 + tax_rate), 2)", "round(subtotal * (1 + tax_rate) + 1, 2)")
    commit(path, {"shop/orders.py": new}, "change total", "2026-09-01T10:00:00Z")
    reindex(client, auth, repo["id"])
    a = next(x for x in drift(client, auth) if x["doc_path"] == "billing.md")
    (sym,) = a["symbols"]
    assert sym["name"] == "compute_total" and sym["lines"] >= 1
    assert sym["mention"] == "Totals come from `compute_total` and tax."
    assert any(c["subject"] == "change total" for c in sym["changes"]) and sym["changes"][0]["sha"]


def test_several_alerts_can_be_marked_reviewed_at_once_but_only_by_someone_who_may_edit(client, auth, shop, session):  # noqa: F811
    path, _ = shop
    repo = add_repo(client, auth, path, name="shop")
    col = make_collection(client, auth, "handbook")
    upload(client, auth, col["id"], {"a.md": "# A\n`compute_total` here.", "b.md": "# B\n`compute_total` there."})
    new = ORDERS.replace("round(subtotal * (1 + tax_rate), 2)", "round(subtotal * (1 + tax_rate) + 1, 2)")
    commit(path, {"shop/orders.py": new}, "change total", "2026-09-01T10:00:00Z")
    reindex(client, auth, repo["id"])
    ids = [a["id"] for a in drift(client, auth) if a["doc_repo"] == "handbook"]
    assert len(ids) == 2
    viewer = make_user(client, auth, "v@acme.io", "viewer")
    assert client.post("/api/wiki/drift/dismiss-all", headers=viewer, json={"ids": ids}).status_code == 403
    r = client.post("/api/wiki/drift/dismiss-all", headers=auth, json={"ids": [*ids, "nope"]})
    assert r.status_code == 200 and r.json() == {"dismissed": 2}
    assert [a for a in drift(client, auth) if a["doc_repo"] == "handbook"] == []
    assert len(drift(client, auth, "dismissed")) == 2
    assert client.post("/api/wiki/drift/dismiss-all", headers=auth, json={"ids": []}).status_code == 422
