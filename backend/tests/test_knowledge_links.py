"""The Knowledge graph also draws the proven links between repositories, and a guess is not called a fact."""

import pytest

from repolens.services.graph_insights import trust
from tests.test_chat import make_user
from tests.test_groups import libs  # noqa: F401  (fixture: the shop and the library it imports)


@pytest.mark.parametrize(
    ("provenance", "weight", "expected"),
    [
        ("static_analysis", 1.0, "fact"),
        ("static_analysis", 0.9, "fact"),
        ("static_analysis", 0.6, "inferred"),
        ("static_analysis", 0.4, "uncertain"),
        ("doc_link", 0.3, "fact"),
        ("git", 1.0, "fact"),
        ("semantic", 0.6, "inferred"),
        ("semantic", 0.3, "uncertain"),
    ],
)
def test_a_call_matched_by_a_name_alone_is_not_called_a_fact(provenance, weight, expected):
    assert trust(provenance, weight) == expected


def links(overview):
    return [e for e in overview["edges"] if e["provenance"] == "repo_link"]


def test_the_overview_draws_the_proven_links_between_repositories_with_their_ends(client, auth, libs):  # noqa: F811
    shop, lib = libs
    o = client.get("/api/graph/overview", headers=auth).json()
    found = links(o)
    assert found and {e["rel"] for e in found} >= {"USES", "IMPORTS"}
    nodes = {n["id"]: n for n in o["nodes"]}
    uses = next(e for e in found if e["rel"] == "USES" and nodes[e["source"]]["name"] == "checkout")
    assert nodes[uses["source"]]["repo_id"] == shop["id"] and nodes[uses["target"]]["repo_id"] == lib["id"]
    assert uses["trust"] == "fact" and "from userlib import" in uses["evidence"]
    assert all(e["source"] in nodes and e["target"] in nodes for e in found)  # no arrow to a node that is not drawn


def test_one_repository_alone_has_no_links_to_others_and_a_hidden_repository_is_left_out(client, auth, libs):  # noqa: F811
    shop, lib = libs
    assert links(client.get("/api/graph/overview", headers=auth, params={"repo_id": shop["id"]}).json()) == []
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.put(f"/api/repositories/{lib['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []})
    seen = client.get("/api/graph/overview", headers=ann).json()
    assert links(seen) == [] and all(n["repo_id"] != lib["id"] for n in seen["nodes"])
