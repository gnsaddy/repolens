"""Stored dependencies between repositories: what a change breaks, what a repository offers, what is new."""

import subprocess

from repolens import config
from repolens.models import LinkState, RepoLink, Repository
from repolens.services import repo_links as rl
from tests.test_api import add_repo
from tests.test_chat import make_user
from tests.test_groups import libs, make_shop, make_userlib, pair  # noqa: F401  (fixtures)


def ids(*repos):
    return ",".join(r["id"] for r in repos)


def org_of(session, repo):
    return session.get(Repository, repo["id"]).org_id


def reindex(client, auth, repo):
    assert client.post(f"/api/repositories/{repo['id']}/index", headers=auth).status_code == 202


def commit_all(path, message):
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    cmd = ["git", "-C", str(path), "-c", "user.name=d", "-c", "user.email=d@x.io", "commit", "-qm", message]
    subprocess.run(cmd, check=True)


def node_named(client, auth, repos, name):
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": ids(*repos)}).json()
    return next(n for n in g["nodes"] if n["name"] == name)


# --- the store ---------------------------------------------------------------------------------------------------


def test_links_are_worked_out_once_and_kept(client, auth, libs, session):  # noqa: F811
    shop, lib = libs
    client.get("/api/groups/analysis", headers=auth, params={"repo_ids": ids(shop, lib)})
    session.expire_all()
    before = session.query(RepoLink).count()
    state = session.get(LinkState, org_of(session, shop))
    stamp = state.computed_at
    assert before >= 5 and {r.kind for r in session.query(RepoLink)} == {"import"}
    client.get("/api/groups/analysis", headers=auth, params={"repo_ids": ids(shop, lib)})
    session.expire_all()
    assert session.query(RepoLink).count() == before and session.get(LinkState, state.org_id).computed_at == stamp


def test_a_link_comes_with_the_proof_and_where_it_starts_and_ends(client, auth, libs, session):  # noqa: F811
    shop, lib = libs
    client.get("/api/groups/analysis", headers=auth, params={"repo_ids": ids(shop, lib)})
    row = session.query(RepoLink).filter_by(src_label="shop/checkout.py:checkout", rel="USES").first()
    assert row.dst_label.startswith("userlib/core.py:") and "from userlib import get_user, UserClient" in row.evidence
    assert (row.trust, row.tier, row.gone_at) == ("fact", "used", None)


def test_a_detector_that_fails_does_not_wipe_what_it_found_before(client, auth, pair, session, monkeypatch):  # noqa: F811
    orders, pricing = pair
    client.get("/api/groups/analysis", headers=auth, params={"repo_ids": ids(orders, pricing)})
    assert session.query(RepoLink).filter_by(kind="http", gone_at=None).count() == 1

    def boom(self, db, repo_ids):
        raise RuntimeError("detector crashed")

    monkeypatch.setattr(rl.HttpDetector, "detect", boom)
    state = rl.refresh(session, org_of(session, orders), force=True)
    session.expire_all()
    assert session.query(RepoLink).filter_by(kind="http", gone_at=None).count() == 1
    assert state.coverage["_failed"] == ["http"]


def test_coverage_says_which_languages_a_detector_cannot_read(client, auth, libs, session):  # noqa: F811
    shop, lib = libs
    path = session.get(Repository, shop["id"]).url
    with open(f"{path}/shop/main.go", "w") as f:
        f.write("package main\n\nfunc main() {\n}\n")
    commit_all(path, "add go")
    reindex(client, auth, shop)
    a = client.get("/api/groups/analysis", headers=auth, params={"repo_ids": ids(shop, lib)}).json()
    cov = a["coverage"]
    assert cov[lib["id"]]["provides"] == ["userlib"] and "python" in cov[lib["id"]]["languages"]
    assert {"detector": "imports", "language": "go", "files": 1} in cov[shop["id"]]["unread"]
    assert not any(u["language"] == "python" for u in cov[shop["id"]]["unread"])


def test_deleting_a_repository_removes_its_links(client, auth, libs, session):  # noqa: F811
    shop, lib = libs
    client.get("/api/groups/analysis", headers=auth, params={"repo_ids": ids(shop, lib)})
    assert client.delete(f"/api/repositories/{lib['id']}", headers=auth).status_code == 204
    session.expire_all()
    assert session.query(RepoLink).count() == 0 and session.get(LinkState, org_of(session, shop)).fingerprint == ""


def test_an_index_refreshes_the_links_without_anyone_opening_the_page(client, auth, tmp_path, monkeypatch, session):
    monkeypatch.setenv("REPOLENS_AUTO_LINK_REFRESH", "true")
    config.get_settings.cache_clear()
    add_repo(client, auth, make_userlib(tmp_path), name="users-lib")
    add_repo(client, auth, make_shop(tmp_path), name="shop-app")
    session.expire_all()
    assert session.query(RepoLink).filter_by(kind="import").count() >= 5


def test_several_indexes_finishing_together_push_one_refresh_back(monkeypatch):
    monkeypatch.setenv("REPOLENS_AUTO_LINK_REFRESH", "true")
    monkeypatch.setenv("REPOLENS_RUN_JOBS_INLINE", "false")
    config.get_settings.cache_clear()
    made = []

    class Spy:
        def __init__(self, wait, fn, args=()):
            self.cancelled, self.daemon = False, False
            made.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    rl.after_index("org-1", delay=30, timer=Spy)
    rl.after_index("org-1", delay=30, timer=Spy)
    assert [t.cancelled for t in made] == [True, False]
    rl._waiting.clear()


# --- what breaks if I change this ---------------------------------------------------------------------------------


def test_blast_radius_lists_what_depends_on_a_function_and_who_changes_it(client, auth, libs):  # noqa: F811
    shop, lib = libs
    get_user = node_named(client, auth, libs, "get_user")
    r = client.get("/api/groups/impact", headers=auth, params={"node_id": get_user["id"]})
    assert r.status_code == 200, r.text
    out = r.json()
    labels = {d["label"] for d in out["direct"]}
    assert {"shop/checkout.py:checkout", "shop/checkout.py:audit", "shop/admin.py:lookup"} <= labels
    assert out["repos"] == [{"repo": "shop-app", "uses": 3}] and out["node"]["repo"] == "users-lib"
    checkout = next(d for d in out["direct"] if d["label"].endswith(":checkout"))
    assert (
        checkout["owners"] == [{"name": "d", "commits": 1}] and "from userlib import get_user" in checkout["evidence"]
    )
    # one step further in: a function in the shop that calls checkout, which depends on get_user
    assert {
        "repo": "shop-app",
        "label": "shop/checkout.py:run_checkout",
        "through": "shop/checkout.py:checkout",
    } in out["indirect"]


def test_blast_radius_of_a_file_covers_everything_defined_in_it(client, auth, libs):  # noqa: F811
    shop, lib = libs
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": ids(shop, lib)}).json()
    core = next(n for n in g["nodes"] if n["type"] == "file" and n["path"] == "userlib/core.py")
    out = client.get("/api/groups/impact", headers=auth, params={"node_id": core["id"]}).json()
    assert {d["target"].split(":")[1] for d in out["direct"]} >= {"get_user", "UserClient"}


def test_blast_radius_counts_but_never_names_a_repository_someone_cannot_see(client, auth, libs):  # noqa: F811
    shop, lib = libs
    get_user = node_named(client, auth, libs, "get_user")
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.put(
        f"/api/repositories/{shop['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []}
    )
    out = client.get("/api/groups/impact", headers=ann, params={"node_id": get_user["id"]}).json()
    assert out["direct"] == [] and out["hidden"] >= 3 and "shop" not in str(out)
    client.put(f"/api/repositories/{lib['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []})
    assert client.get("/api/groups/impact", headers=ann, params={"node_id": get_user["id"]}).status_code == 404


# --- what a repository offers -------------------------------------------------------------------------------------


def test_public_surface_ranks_what_others_use_and_finds_exports_nobody_uses(client, auth, libs):  # noqa: F811
    shop, lib = libs
    out = client.get("/api/groups/surface", headers=auth, params={"repo_id": lib["id"]}).json()
    by = {s["name"]: s for s in out["symbols"]}
    assert by["get_user"]["uses"] >= 3 and by["get_user"]["repos"] == ["shop-app"]
    assert by["UserClient"]["uses"] >= 2 and out["symbols"][0]["uses"] >= out["symbols"][-1]["uses"]
    assert out["exports"] == 3 and [e["name"] for e in out["unused_exports"]] == ["unused_helper"]
    assert out["dependants"] == ["shop-app"]


def test_the_surface_of_a_repository_nobody_can_see_is_not_found(client, auth, libs):  # noqa: F811
    shop, lib = libs
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.put(f"/api/repositories/{lib['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []})
    assert client.get("/api/groups/surface", headers=ann, params={"repo_id": lib["id"]}).status_code == 404


# --- what is new --------------------------------------------------------------------------------------------------


def test_nothing_is_called_new_the_first_time_the_links_are_worked_out(client, auth, libs):  # noqa: F811
    shop, lib = libs
    r = client.get("/api/groups/changes", headers=auth, params={"repo_ids": ids(shop, lib), "days": 7}).json()
    assert r["new"] == [] and r["removed"] == [] and r["tracking_since"]


def test_a_new_dependency_after_a_reindex_is_reported_with_its_proof(client, auth, libs, session):  # noqa: F811
    shop, lib = libs
    client.get("/api/groups/changes", headers=auth, params={"repo_ids": ids(shop, lib)})  # tracking starts
    path = session.get(Repository, shop["id"]).url
    with open(f"{path}/shop/reports.py", "w") as f:
        f.write("from userlib import unused_helper\n\n\ndef report():\n    return unused_helper()\n")
    commit_all(path, "use the helper")
    reindex(client, auth, shop)
    r = client.get("/api/groups/changes", headers=auth, params={"repo_ids": ids(shop, lib)}).json()
    (pair_,) = r["new"]
    assert (pair_["from"], pair_["to"], pair_["count"]) == ("shop-app", "users-lib", 1), r
    ex = pair_["examples"][0]
    assert ex["from"] == "shop/reports.py:report" and "from userlib import unused_helper" in ex["evidence"]
    assert r["removed"] == []
    out = client.get("/api/groups/surface", headers=auth, params={"repo_id": lib["id"]}).json()
    assert out["unused_exports"] == []  # now that something uses it


def test_a_dependency_that_goes_away_is_reported_as_removed(client, auth, libs, session):  # noqa: F811
    shop, lib = libs
    client.get("/api/groups/changes", headers=auth, params={"repo_ids": ids(shop, lib)})
    path = session.get(Repository, shop["id"]).url
    with open(f"{path}/shop/admin.py", "w") as f:
        f.write("def lookup(uid):\n    return uid\n")
    commit_all(path, "stop using the library here")
    reindex(client, auth, shop)
    r = client.get("/api/groups/changes", headers=auth, params={"repo_ids": ids(shop, lib)}).json()
    assert r["new"] == [] and [(p["from"], p["to"]) for p in r["removed"]] == [("shop-app", "users-lib")]
    assert r["removed"][0]["examples"][0]["from"] == "shop/admin.py:lookup"


def test_a_caller_is_the_function_never_the_file_that_shares_its_chunk(client, auth, libs, session):  # noqa: F811
    """A file's node takes the id of the file's first chunk, so that chunk belongs to two nodes. Which one a lookup
    returned depended on the order the database gave its rows: SQLite gave the function, PostgreSQL on CI the file."""
    from repolens.models import CodeChunk, KGNode

    shop, lib = libs
    first = (
        session.query(CodeChunk)
        .filter_by(repo_id=shop["id"], file_path="shop/checkout.py", symbol_name="checkout")
        .one()
    )
    owners = session.query(KGNode).filter_by(chunk_id=first.id).all()
    assert {n.type for n in owners} >= {"function"}  # the function opens the file, so the file node shares its chunk
    mapped = rl._symbol_nodes(session, [first.id])
    assert session.get(KGNode, mapped[first.id]).type == "function"
    g = client.get(
        "/api/groups/code-graph", headers=auth, params={"repo_ids": ",".join([shop["id"], lib["id"]])}
    ).json()
    by_id = {n["id"]: n for n in g["nodes"]}
    sources = {by_id[e["source"]]["name"] for e in g["edges"] if e["rel"] == "USES"}
    assert {"checkout", "audit"} <= sources and "checkout.py" not in sources
