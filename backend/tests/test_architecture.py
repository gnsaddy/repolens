"""Architecture rules and cycles of a saved system."""

import pytest

from repolens.models import ArchRule
from repolens.services import architecture as arch
from tests.test_chat import make_user
from tests.test_groups import create
from tests.test_lang_imports import two


def link(src, dst, **kw):
    base = {"src": src, "dst": dst, "strong": 0, "http": 0, "imports": 0, "declared": 0, "infra": 0, "weak": 0}
    return {**base, "trust": "fact", "rels": {}, "examples": [{"rel": "USES", "evidence": f"{src} uses {dst}"}], **kw}


NAMES = {"a": "alpha", "b": "beta", "c": "gamma", "d": "delta"}


def test_a_loop_of_repositories_is_found_with_the_proof_of_each_step():
    links = [link("a", "b", imports=1), link("b", "c", http=2), link("c", "a", declared=1), link("c", "d", imports=1)]
    (loop,) = arch.cycles(links, NAMES)
    assert loop["repos"] == ["a", "b", "c"]
    assert {(e["src"], e["dst"]) for e in loop["edges"]} == {("a", "b"), ("b", "c"), ("c", "a")}
    assert all(e["proof"] for e in loop["edges"])


def test_a_chain_is_not_a_loop_and_name_only_matches_do_not_make_one():
    chain = [link("a", "b", imports=1), link("b", "c", imports=1)]
    assert arch.cycles(chain, NAMES) == []
    guess = [link("a", "b", imports=1), link("b", "a", weak=9)]
    assert arch.cycles(guess, NAMES) == []


@pytest.fixture
def ring(client, auth, tmp_path):
    """Two Python repositories that import each other."""

    def pkg(me, other):
        return {
            f"{me}/__init__.py": f"from .core import {me}_fn as {me}_fn\n",
            f"{me}/core.py": f"from {other} import {other}_fn\n\n\ndef {me}_fn():\n    return {other}_fn()\n",
        }

    alpha, beta = two(client, auth, tmp_path, pkg("alphapkg", "betapkg"), pkg("betapkg", "alphapkg"))
    return alpha, beta, create(client, auth, [alpha["id"], beta["id"]], name="Ring")


def rule(client, h, gid, src, dst, note=""):
    return client.post(f"/api/groups/{gid}/rules", headers=h, json={"src": src["id"], "dst": dst["id"], "note": note})


def test_a_broken_rule_comes_with_the_import_that_breaks_it_and_the_loop_is_reported(client, auth, ring):
    alpha, beta, g = ring
    assert rule(client, auth, g["id"], alpha, beta, "alpha stays below beta").status_code == 201
    r = client.get(f"/api/groups/{g['id']}/architecture", headers=auth).json()
    (one,) = r["rules"]
    assert one["broken"] and one["violation"]["links"] >= 1 and one["note"] == "alpha stays below beta"
    assert "betapkg" in one["violation"]["examples"][0]["evidence"]
    assert r["broken"] == 1 and len(r["cycles"]) == 1 and set(r["cycles"][0]["names"]) == {"provider", "consumer"}


def test_a_rule_nobody_breaks_is_reported_as_kept(client, auth, tmp_path):
    a, b = two(client, auth, tmp_path, {"a.py": "def x():\n    return 1\n"}, {"b.py": "def y():\n    return 2\n"})
    g = create(client, auth, [a["id"], b["id"]], name="Apart")
    rule(client, auth, g["id"], a, b)
    r = client.get(f"/api/groups/{g['id']}/architecture", headers=auth).json()
    assert r["rules"][0]["broken"] is False and r["broken"] == 0 and r["cycles"] == []


def test_rules_are_checked_when_added_and_can_be_removed(client, auth, ring, session):
    alpha, beta, g = ring
    assert rule(client, auth, g["id"], alpha, alpha).status_code == 422
    assert rule(client, auth, g["id"], alpha, {"id": "nope"}).status_code == 404
    assert rule(client, auth, g["id"], alpha, beta).status_code == 201
    assert rule(client, auth, g["id"], alpha, beta).status_code == 409
    rid = session.query(ArchRule).one().id
    assert client.delete(f"/api/groups/{g['id']}/rules/{rid}", headers=auth).status_code == 204
    assert client.delete(f"/api/groups/{g['id']}/rules/{rid}", headers=auth).status_code == 404
    assert session.query(ArchRule).count() == 0


def test_only_an_admin_or_the_author_changes_rules_but_a_viewer_reads_them(client, auth, ring):
    alpha, beta, g = ring
    rule(client, auth, g["id"], alpha, beta)
    viewer = make_user(client, auth, "v@acme.io", "viewer")
    ann = make_user(client, auth, "ann@acme.io", "member")
    assert rule(client, viewer, g["id"], beta, alpha).status_code == 403
    assert rule(client, ann, g["id"], beta, alpha).status_code == 403  # saved by someone else
    assert len(client.get(f"/api/groups/{g['id']}/architecture", headers=viewer).json()["rules"]) == 1


def test_a_rule_about_a_repository_someone_cannot_open_is_counted_never_named(client, auth, ring):
    alpha, beta, g = ring
    rule(client, auth, g["id"], alpha, beta, "secret note")
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.put(
        f"/api/repositories/{beta['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []}
    )
    r = client.get(f"/api/groups/{g['id']}/architecture", headers=ann).json()
    assert r["rules"] == [] and r["hidden_rules"] == 1 and r["cycles"] == [] and r["checked"] == 1
    assert "beta" not in str(r) and "secret note" not in str(r)


def test_deleting_a_repository_removes_its_rules(client, auth, ring, session):
    alpha, beta, g = ring
    rule(client, auth, g["id"], alpha, beta)
    assert client.delete(f"/api/repositories/{beta['id']}", headers=auth).status_code in (200, 204)
    session.expire_all()
    assert session.query(ArchRule).count() == 0


def test_the_dependencies_export_as_mermaid_and_json_with_only_proven_links(client, auth, ring):
    alpha, beta, g = ring
    q = {"repo_ids": f"{alpha['id']},{beta['id']}"}
    m = client.get("/api/groups/export", headers=auth, params=q).json()
    assert m["format"] == "mermaid" and m["content"].startswith("graph LR\n")
    assert '["provider"]' in m["content"] and "-->" in m["content"]
    j = client.get("/api/groups/export", headers=auth, params={**q, "format": "json"}).json()
    import json

    body = json.loads(j["content"])
    assert {r["name"] for r in body["repositories"]} == {"provider", "consumer"}
    assert all(d["links"] >= 1 for d in body["dependencies"]) and len(body["dependencies"]) == 2
    assert client.get("/api/groups/export", headers=auth, params={**q, "format": "svg"}).status_code == 422


def test_mermaid_labels_cannot_break_out_of_the_diagram():
    a = {"repos": [{"id": "x", "name": 'bad"]\n  click n0 call evil()'}], "links": []}
    out = arch.export(a, "mermaid")["content"]
    assert out.count("\n") == 3 and "evil" in out and '"]' not in out.split("\n")[1][:-2]


def test_code_graph_nodes_carry_the_heat_of_their_file(client, auth, ring, session):
    from datetime import UTC, datetime

    from repolens.models import Change

    alpha, beta, _g = ring
    for n in range(3):
        session.add(
            Change(
                repo_id=alpha["id"],
                org_id=org_id(session, alpha),
                sha=f"s{n}" * 8,
                author="ann",
                committed_at=datetime.now(UTC),
                message="fix",
                files=[{"path": "alphapkg/core.py", "additions": 1, "deletions": 0}],
            )
        )
    session.commit()
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": f"{alpha['id']},{beta['id']}"}).json()
    by_path = {}
    for n in g["nodes"]:
        by_path.setdefault((n["repo_id"], n["path"]), g["heat"][n["id"]])
    assert by_path[(alpha["id"], "alphapkg/core.py")] == 1.0
    assert 0 < by_path[(beta["id"], "betapkg/core.py")] < 1 and g["heat_days"] == 90  # its one commit against four


def org_id(session, repo):
    from repolens.models import Repository

    return session.get(Repository, repo["id"]).org_id
