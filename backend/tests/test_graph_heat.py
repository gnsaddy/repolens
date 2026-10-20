from tests.conftest import register
from tests.test_api import add_repo
from tests.test_risk import shop  # noqa: F401  (fixture)


def nodes_of(client, auth, repo_id):
    return client.get("/api/graph/overview", headers=auth, params={"repo_id": repo_id, "limit": 400}).json()["nodes"]


def heat(client, auth, nodes, metric, days=3650):
    r = client.post(
        "/api/graph/heat", headers=auth, json={"metric": metric, "node_ids": [n["id"] for n in nodes], "days": days}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_heat_metrics(client, auth, shop):  # noqa: F811
    path, _ = shop
    repo = add_repo(client, auth, path, name="shop")
    nodes = nodes_of(client, auth, repo["id"])
    by_path = {n["path"]: n for n in nodes if n["type"] == "file"}

    churn = heat(client, auth, nodes, "churn")
    hottest = max(churn["raw"], key=churn["raw"].get)
    assert (
        churn["values"][hottest] == 1.0
        and next(n for n in nodes if n["id"] == hottest)["path"] == "shop/auth/tokens.py"
    )
    assert 0 < churn["values"][by_path["shop/orders.py"]["id"]] < 1
    assert all(0 <= v <= 1 for v in churn["values"].values())
    assert heat(client, auth, nodes, "churn", days=1)["values"] == {}  # nothing in the last day

    bugs = heat(client, auth, nodes, "bugs")
    assert bugs["values"][by_path["shop/orders.py"]["id"]] == 1.0  # both fix commits touched it
    assert by_path["shop/reports.py"]["id"] not in bugs["values"]  # feature commit only

    conn = heat(client, auth, nodes, "connections")
    top = max(nodes, key=lambda n: n["degree"])
    assert conn["values"][top["id"]] == 1.0

    undoc = heat(client, auth, nodes, "undocumented")
    fn = {n["name"]: n["id"] for n in nodes if n["type"] == "function"}
    assert (
        set(undoc["values"].values()) <= {0.0, 1.0}
        and fn
        and set(undoc["values"]) == {n["id"] for n in nodes if n["type"] in ("function", "class")}
    )


def test_heat_validation_and_isolation(client, auth, shop):  # noqa: F811
    path, _ = shop
    repo = add_repo(client, auth, path, name="shop")
    nodes = nodes_of(client, auth, repo["id"])[:3]
    assert client.post("/api/graph/heat", headers=auth, json={"metric": "nope", "node_ids": ["x"]}).status_code == 422
    assert client.post("/api/graph/heat", headers=auth, json={"metric": "churn", "node_ids": []}).status_code == 422
    other = register(client, "x@other.io", "Other")
    r = client.post(
        "/api/graph/heat", headers=other, json={"metric": "connections", "node_ids": [n["id"] for n in nodes]}
    )
    assert (
        r.status_code == 200 and r.json()["values"] == {} and r.json()["raw"] == {}
    )  # other tenants' nodes are invisible
