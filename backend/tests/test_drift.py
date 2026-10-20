from repolens.models import DriftAlert
from tests.conftest import register
from tests.test_api import add_repo
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
