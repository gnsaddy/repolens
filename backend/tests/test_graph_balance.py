import subprocess

from sqlalchemy import select

from repolens.models import KGNode, Organization
from tests.test_api import add_repo


def tiny_repo(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    (d / "a.py").write_text("def one():\n    return two()\n\n\ndef two():\n    return 2\n")
    (d / "README.md").write_text(f"# {name}\n\nSee a.py.\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(d), *cmd], check=True)
    return d


def test_a_big_repository_does_not_crowd_a_small_one_out_of_the_combined_graph(client, auth, session, tmp_path):
    big = add_repo(client, auth, str(tiny_repo(tmp_path, "big")), name="big")
    small = add_repo(client, auth, str(tiny_repo(tmp_path, "small")), name="small")
    org = session.query(Organization).first().id
    for i in range(40):  # far more connected than anything in the small repository
        session.add(
            KGNode(id=f"x{i:030d}", org_id=org, repo_id=big["id"], type="function", name=f"f{i}", degree=100 + i)
        )
    session.commit()
    kinds = ["file", "doc", "section", "function", "class"]  # what the graph shows by default (commits are hidden)
    mine = {
        n.id
        for n in session.scalars(
            select(KGNode).where(KGNode.repo_id == small["id"], KGNode.degree > 0, KGNode.type.in_(kinds))
        )
    }
    assert len(mine) >= 4  # the small repository has a graph of its own

    alone = client.get(f"/api/graph/overview?repo_id={small['id']}&limit=10", headers=auth).json()["nodes"]
    assert len(alone) == min(10, len(mine)) and {n["id"] for n in alone} <= mine

    everything = client.get("/api/graph/overview?limit=10", headers=auth).json()["nodes"]
    from_small = [n for n in everything if n["repo_id"] == small["id"]]
    assert len(from_small) >= min(10, len(mine)), "the small repository was crowded out"  # before: none of them
    assert len(everything) <= 20  # the budget grows with the number of repositories, it does not run away
    assert {n["repo_id"] for n in everything} == {big["id"], small["id"]}


def test_one_repository_still_gets_exactly_its_most_connected_nodes(client, auth, session, tmp_path):
    big = add_repo(client, auth, str(tiny_repo(tmp_path, "solo")), name="solo")
    org = session.query(Organization).first().id
    for i in range(30):
        session.add(
            KGNode(id=f"y{i:030d}", org_id=org, repo_id=big["id"], type="function", name=f"g{i}", degree=100 + i)
        )
    session.commit()
    got = client.get("/api/graph/overview?limit=10", headers=auth).json()["nodes"]
    assert len(got) == 10 and min(n["degree"] for n in got) >= 120  # the ten highest degrees, as before


def test_the_combined_graph_never_exceeds_the_hard_cap(client, auth, session, tmp_path):
    org = session.query(Organization).first().id
    ids = []
    for k in range(3):
        r = add_repo(client, auth, str(tiny_repo(tmp_path, f"r{k}")), name=f"r{k}")
        ids.append(r["id"])
        for i in range(200):
            session.add(
                KGNode(id=f"{k}{i:031d}", org_id=org, repo_id=r["id"], type="function", name=f"n{i}", degree=50 + i)
            )
    session.commit()
    got = client.get("/api/graph/overview?limit=400", headers=auth).json()["nodes"]
    assert len(got) == 400 and {n["repo_id"] for n in got} == set(ids)
