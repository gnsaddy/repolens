"""Systems: saved sets of repositories, how they depend on each other and change together, and a written overview."""

from datetime import UTC, datetime, timedelta

import pytest

from repolens.models import GroupOverview, RepoGroup
from repolens.services import groups as svc
from repolens.services.llm import LLMClient, LLMResult
from tests.test_api import add_repo
from tests.test_chat import make_user
from tests.test_risk import commit, git


def days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_pricing(tmp_path):
    p = tmp_path / "pricing-svc"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(
        p,
        {
            "pricing/engine.py": "def compute_total(order):\n    return sum(order)\n",
            "pricing/api.py": (
                "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
                '@app.get("/pricing/quote/{order_id}")\ndef quote(order_id):\n    return {"total": 1}\n'
            ),
        },
        "ORD-7: price engine",
        days_ago(3),
    )
    commit(
        p,
        {"pricing/engine.py": "def compute_total(order):\n    return round(sum(order), 2)\n"},
        "fix: rounding of totals",
        days_ago(1),
    )
    return p


def make_orders(tmp_path):
    p = tmp_path / "orders-svc"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(
        p,
        {
            "orders/handlers.py": "def handle_order(order):\n    return compute_total(order)\n",
            "orders/client.py": (
                "PRICING_URL = 'http://pricing'\n\n\n"
                'def fetch_quote(order_id):\n    return http_get(f"{PRICING_URL}/pricing/quote/{order_id}")\n'
            ),
        },
        "ORD-7: handle orders",
        days_ago(3),
    )
    return p


@pytest.fixture
def pair(client, auth, tmp_path):
    pricing = add_repo(client, auth, make_pricing(tmp_path), name="pricing-svc")
    orders = add_repo(
        client, auth, make_orders(tmp_path), name="orders-svc"
    )  # indexed second: it sees the pricing code
    return orders, pricing


def create(client, h, ids, name="Checkout", **kw):
    r = client.post("/api/groups", headers=h, json={"name": name, "repo_ids": ids, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_the_analysis_shows_who_depends_on_whom_with_the_code_that_proves_it(client, auth, pair):
    orders, pricing = pair
    a = client.get("/api/groups/analysis", headers=auth, params={"repo_ids": f"{orders['id']},{pricing['id']}"}).json()
    (link,) = [x for x in a["links"] if x["src"] == orders["id"] and x["dst"] == pricing["id"]]
    assert link["rels"]["CALLS"] >= 1 and link["rels"]["HTTP"] == 1
    assert link["http"] == 1 and link["weak"] >= 1 and link["trust"] == "inferred"  # not "fact": no import proves it
    seen = {e["rel"]: e for e in link["examples"]}
    assert "orders/handlers.py" in seen["CALLS"]["from"] and "pricing/engine.py" in seen["CALLS"]["to"]
    assert "orders/client.py:fetch_quote" in seen["HTTP"]["from"] and "pricing/api.py:quote" in seen["HTTP"]["to"]
    assert not [x for x in a["links"] if x["src"] == pricing["id"]]  # nothing in pricing depends on orders


def test_a_url_that_matches_another_repositorys_route_is_a_link_over_http(client, auth, pair):
    orders, pricing = pair
    a = client.get("/api/groups/analysis", headers=auth, params={"repo_ids": f"{pricing['id']},{orders['id']}"}).json()
    (link,) = [x for x in a["links"] if x["rels"].get("HTTP")]
    assert (link["src"], link["dst"]) == (orders["id"], pricing["id"])
    ev = next(e["evidence"] for e in link["examples"] if e["rel"] == "HTTP")
    assert "fetch_quote uses the URL /pricing/quote/{order_id}" in ev and "GET /pricing/quote/{order_id}" in ev


def test_the_code_graph_shows_the_functions_on_both_ends_of_each_link(client, auth, pair):
    orders, pricing = pair
    q = {"repo_ids": f"{orders['id']},{pricing['id']}"}
    g = client.get("/api/groups/code-graph", headers=auth, params=q).json()
    by = {n["name"]: n for n in g["nodes"]}
    assert by["fetch_quote"]["repo"] == "orders-svc" and by["quote"]["repo"] == "pricing-svc"
    (http,) = [e for e in g["edges"] if e["rel"] == "HTTP"]
    assert (http["source"], http["target"], http["trust"]) == (by["fetch_quote"]["id"], by["quote"]["id"], "inferred")
    assert "orders/client.py" in {n["path"] for n in g["nodes"]}  # the file that holds the caller is drawn with it
    assert g["available"] == {"strong": 1, "weak": 1} and g["shown_weak"] is False
    assert "handle_order" not in by  # a call matched by name alone is not drawn unless asked for
    more = client.get("/api/groups/code-graph", headers=auth, params={**q, "weak": "true"}).json()
    (guess,) = [e for e in more["edges"] if e["rel"] == "CALLS" and e["trust"] == "uncertain"]
    assert {n["name"] for n in more["nodes"]} >= {"handle_order", "compute_total"} and guess["weight"] < 0.8


def test_the_code_graph_can_be_narrowed_to_one_direction_of_one_pair(client, auth, pair):
    orders, pricing = pair
    ids = f"{orders['id']},{pricing['id']}"
    ok = client.get(
        "/api/groups/code-graph", headers=auth, params={"repo_ids": ids, "src": orders["id"], "dst": pricing["id"]}
    )
    assert [e["rel"] for e in ok.json()["edges"] if e["rel"] == "HTTP"] == ["HTTP"]
    back = client.get(
        "/api/groups/code-graph", headers=auth, params={"repo_ids": ids, "src": pricing["id"], "dst": orders["id"]}
    )
    assert back.json()["edges"] == [] and back.json()["available"] == {"strong": 0, "weak": 0}


def test_the_code_graph_never_draws_a_repository_someone_cannot_see(client, auth, pair):
    orders, pricing = pair
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.put(
        f"/api/repositories/{pricing['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []}
    )
    r = client.get("/api/groups/code-graph", headers=ann, params={"repo_ids": f"{orders['id']},{pricing['id']}"})
    assert r.status_code == 404


def test_repositories_that_share_a_ticket_or_a_person_and_day_are_said_to_change_together(client, auth, pair):
    orders, pricing = pair
    a = client.get("/api/groups/analysis", headers=auth, params={"repo_ids": f"{orders['id']},{pricing['id']}"}).json()
    (c,) = a["coupling"]
    assert c["tickets"] == 1 and c["ticket_examples"] == ["ORD-7"] and c["days"] >= 1


def test_where_change_and_trouble_concentrate_is_counted_per_repository_with_a_heat(client, auth, pair):
    orders, pricing = pair
    a = client.get("/api/groups/analysis", headers=auth, params={"repo_ids": f"{orders['id']},{pricing['id']}"}).json()
    by = {r["name"]: r for r in a["repos"]}
    assert by["pricing-svc"]["risk"]["commits"] == 2 and by["pricing-svc"]["risk"]["fixes"] == 1
    assert by["orders-svc"]["risk"]["fixes"] == 0
    assert by["pricing-svc"]["heat"]["fixes"] == 1.0 and by["orders-svc"]["heat"]["fixes"] == 0.0
    assert a["estimate_tokens"] > 900  # the page can say what writing an overview will cost


def test_a_selection_can_be_saved_listed_renamed_and_deleted_with_who_saved_it(client, auth, pair):
    orders, pricing = pair
    g = create(client, auth, [orders["id"], pricing["id"]], description="orders and prices")
    assert g["created_by_name"] == "admin" and g["hidden"] == 0
    listed = client.get("/api/groups", headers=auth).json()["groups"]
    assert [x["name"] for x in listed] == ["Checkout"]
    saved = client.get(f"/api/groups/{g['id']}/analysis", headers=auth).json()
    assert len(saved["repos"]) == 2 and saved["links"]
    r = client.put(f"/api/groups/{g['id']}", headers=auth, json={"name": "Orders", "repo_ids": [orders["id"]]})
    assert r.status_code == 200 and r.json()["name"] == "Orders" and r.json()["repo_ids"] == [orders["id"]]
    assert client.delete(f"/api/groups/{g['id']}", headers=auth).status_code == 204
    assert client.get("/api/groups", headers=auth).json()["groups"] == []


def test_bad_selections_are_refused(client, auth, pair):
    orders, pricing = pair
    create(client, auth, [orders["id"]], name="One")
    dup = client.post("/api/groups", headers=auth, json={"name": "one", "repo_ids": [pricing["id"]]})
    assert dup.status_code == 409  # names are compared without regard to case
    assert client.post("/api/groups", headers=auth, json={"name": "X1", "repo_ids": ["nope"]}).status_code == 404
    too_many = [orders["id"]] * 2 + [f"r{i}" for i in range(svc.MAX_REPOS)]
    assert client.post("/api/groups", headers=auth, json={"name": "Big", "repo_ids": too_many}).status_code == 422
    assert client.post("/api/groups", headers=auth, json={"name": "Empty", "repo_ids": []}).status_code == 422


def test_members_save_systems_viewers_only_look_and_only_the_author_or_an_admin_changes_one(client, auth, pair):
    orders, pricing = pair
    ids = [orders["id"], pricing["id"]]
    ann = make_user(client, auth, "ann@acme.io", "member")
    bob = make_user(client, auth, "bob@acme.io", "member")
    viewer = make_user(client, auth, "v@acme.io", "viewer")
    g = create(client, ann, ids, name="Ann's")
    assert g["created_by_name"] == "ann"  # no display name: the part before the @
    assert client.post("/api/groups", headers=viewer, json={"name": "No", "repo_ids": ids}).status_code == 403
    assert client.get(f"/api/groups/{g['id']}/analysis", headers=viewer).status_code == 200
    body = {"name": "Bob's", "repo_ids": ids}
    assert client.put(f"/api/groups/{g['id']}", headers=bob, json=body).status_code == 403
    assert client.delete(f"/api/groups/{g['id']}", headers=bob).status_code == 403
    assert client.put(f"/api/groups/{g['id']}", headers=auth, json=body).status_code == 200  # an admin may


def test_a_repository_someone_cannot_see_is_counted_never_named_or_analysed(client, auth, pair):
    orders, pricing = pair
    ann = make_user(client, auth, "ann@acme.io", "member")
    g = create(client, auth, [orders["id"], pricing["id"]])
    r = client.put(
        f"/api/repositories/{pricing['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []}
    )
    assert r.status_code == 200, r.text
    seen = client.get("/api/groups", headers=ann).json()["groups"][0]
    assert seen["repo_ids"] == [orders["id"]] and seen["hidden"] == 1
    a = client.get(f"/api/groups/{g['id']}/analysis", headers=ann).json()
    assert [x["name"] for x in a["repos"]] == ["orders-svc"] and a["links"] == []
    assert "pricing" not in str(a)
    assert (
        client.post("/api/groups", headers=ann, json={"name": "Peek", "repo_ids": [pricing["id"]]}).status_code == 404
    )
    other = client.get("/api/groups/analysis", headers=ann, params={"repo_ids": pricing["id"]})
    assert other.status_code == 404
    # editing from a smaller view keeps the repository nobody told them about
    client.put(
        f"/api/groups/{g['id']}", headers=auth, json={"name": "Checkout", "repo_ids": [orders["id"], pricing["id"]]}
    )
    mine = client.post("/api/groups", headers=ann, json={"name": "Mine", "repo_ids": [orders["id"]]}).json()
    assert (
        client.put(
            f"/api/groups/{mine['id']}", headers=ann, json={"name": "Mine2", "repo_ids": [orders["id"]]}
        ).status_code
        == 200
    )


def test_with_no_model_the_overview_is_a_labelled_list_of_facts_signed_by_who_asked(client, auth, pair):
    orders, pricing = pair
    g = create(client, auth, [orders["id"], pricing["id"]])
    ann = make_user(client, auth, "ann@acme.io", "member")
    r = client.post(f"/api/groups/{g['id']}/overview", headers=ann)
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["mode"] == "extractive" and o["created_by_name"] == "ann" and o["repositories"] == 2
    assert "No language model is configured" in o["content"] and "orders-svc -> pricing-svc" in o["content"]
    shared = client.get(f"/api/groups/{g['id']}/overviews", headers=auth).json()  # the whole workspace sees it
    assert [x["created_by_name"] for x in shared["overviews"]] == ["ann"]


def test_a_written_overview_is_checked_for_names_that_are_not_in_the_facts(client, auth, pair, monkeypatch):
    orders, pricing = pair
    g = create(client, auth, [orders["id"], pricing["id"]])
    seen = {}

    def write(this, agent, system, prompt, **k):
        seen["prompt"] = prompt
        return LLMResult("orders-svc calls pricing-svc in `pricing/engine.py` and `ghost/vanished.py`.", 50, 40)

    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", write)
    o = client.post(f"/api/groups/{g['id']}/overview", headers=auth).json()
    assert o["mode"] == "llm" and o["unsupported_names"] == ["ghost/vanished.py"]
    assert "orders/handlers.py" in seen["prompt"] and "FACTS:" in seen["prompt"]


def test_a_failed_model_call_is_reported_and_nothing_is_stored(client, auth, pair, session, monkeypatch):
    orders, pricing = pair
    g = create(client, auth, [orders["id"], pricing["id"]])

    def boom(this, *a, **k):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", boom)
    r = client.post(f"/api/groups/{g['id']}/overview", headers=auth)
    assert r.status_code == 502 and "could not write the overview" in r.json()["detail"]
    assert session.query(GroupOverview).count() == 0


def test_an_overview_built_from_a_repository_someone_cannot_see_is_left_out_for_them(client, auth, pair):
    orders, pricing = pair
    g = create(client, auth, [orders["id"], pricing["id"]])
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.post(f"/api/groups/{g['id']}/overview", headers=auth)
    client.put(
        f"/api/repositories/{pricing['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []}
    )
    seen = client.get(f"/api/groups/{g['id']}/overviews", headers=ann).json()
    assert seen == {"overviews": [], "hidden": 1}
    assert len(client.get(f"/api/groups/{g['id']}/overviews", headers=auth).json()["overviews"]) == 1


def test_only_the_newest_overviews_are_kept(client, auth, pair, session):
    orders, pricing = pair
    g = create(client, auth, [orders["id"], pricing["id"]])
    for _ in range(svc.KEEP_OVERVIEWS + 2):
        assert client.post(f"/api/groups/{g['id']}/overview", headers=auth).status_code == 201
    assert session.query(GroupOverview).count() == svc.KEEP_OVERVIEWS


def test_deleting_a_repository_takes_it_out_of_its_systems_and_drops_what_was_written_from_it(
    client, auth, pair, session
):
    orders, pricing = pair
    g = create(client, auth, [orders["id"], pricing["id"]])
    solo = create(client, auth, [pricing["id"]], name="Solo")
    client.post(f"/api/groups/{g['id']}/overview", headers=auth)
    assert client.delete(f"/api/repositories/{pricing['id']}", headers=auth).status_code == 204
    session.expire_all()
    assert session.get(RepoGroup, g["id"]).repo_ids == [orders["id"]]
    assert session.get(RepoGroup, solo["id"]) is None  # nothing left in it
    assert session.query(GroupOverview).count() == 0


# --- dependencies proved by imports -------------------------------------------------------------------------------


def make_userlib(tmp_path):
    p = tmp_path / "users-lib"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(
        p,
        {
            "userlib/__init__.py": (
                "from .core import get_user as get_user\nfrom .core import UserClient\n"
                "from .core import unused_helper as unused_helper\n"
            ),
            "userlib/core.py": (
                "def get_user(user_id):\n    return {'id': user_id}\n\n\n"
                "class UserClient:\n    def name(self, user):\n        return user['id']\n\n\n"
                "def unused_helper():\n    return None\n"
            ),
        },
        "feat: user library",
        days_ago(5),
    )
    return p


def make_shop(tmp_path):
    p = tmp_path / "shop-app"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(
        p,
        {
            "shop/checkout.py": (
                "from userlib import get_user, UserClient\nimport requests\n\n\n"
                "def checkout(uid):\n    user = get_user(uid)\n    return UserClient().name(user)\n\n\n"
                "def audit(uid):\n    return get_user(uid)\n\n\n"
                "def run_checkout(uid):\n    return checkout(uid)\n"
            ),
            "shop/models.py": "from userlib import UserClient\n\n\nclass Order(UserClient):\n    pass\n",
            "shop/admin.py": "import userlib\n\n\ndef lookup(uid):\n    return userlib.get_user(uid)\n",
            "README.md": "# Shop\n\nIt calls `get_user` from the user library.\n",
        },
        "feat: shop",
        days_ago(4),
    )
    return p


@pytest.fixture
def libs(client, auth, tmp_path):
    lib = add_repo(client, auth, make_userlib(tmp_path), name="users-lib")
    shop = add_repo(client, auth, make_shop(tmp_path), name="shop-app")
    return shop, lib


def test_an_imported_class_or_function_is_linked_to_where_the_other_repository_defines_it(client, auth, libs):
    shop, lib = libs
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": f"{shop['id']},{lib['id']}"}).json()
    by_id = {n["id"]: n for n in g["nodes"]}
    uses = {(by_id[e["source"]]["name"], by_id[e["target"]]["name"]): e for e in g["edges"] if e["rel"] == "USES"}
    # `from userlib import get_user` is answered by userlib/__init__.py, which re-exports it from core.py: the edge goes there
    assert set(uses) >= {("checkout", "get_user"), ("checkout", "UserClient"), ("audit", "get_user")}
    target = by_id[uses[("checkout", "get_user")]["target"]]
    assert (target["repo"], target["path"], target["type"]) == ("users-lib", "userlib/core.py", "function")
    e = uses[("checkout", "get_user")]
    assert e["trust"] == "fact" and "from userlib import get_user, UserClient" in e["evidence"]
    assert by_id[uses[("checkout", "UserClient")]["target"]]["type"] == "class"


def test_a_file_that_only_imports_a_class_still_depends_on_it(client, auth, libs):
    shop, lib = libs
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": f"{shop['id']},{lib['id']}"}).json()
    by_id = {n["id"]: n for n in g["nodes"]}
    (imp,) = [e for e in g["edges"] if e["rel"] == "IMPORTS"]
    assert (by_id[imp["source"]]["path"], by_id[imp["source"]]["type"]) == ("shop/models.py", "file")
    assert by_id[imp["target"]]["name"] == "UserClient" and "shop/models.py imports UserClient" in imp["evidence"]


def test_import_userlib_then_a_call_is_linked_but_marked_inferred(client, auth, libs):
    shop, lib = libs
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": f"{shop['id']},{lib['id']}"}).json()
    by_id = {n["id"]: n for n in g["nodes"]}
    (e,) = [x for x in g["edges"] if x["rel"] == "USES" and by_id[x["source"]]["name"] == "lookup"]
    assert e["trust"] == "inferred" and "import userlib" in e["evidence"]


def test_the_repository_link_counts_imports_as_the_proof_and_ignores_packages_nobody_in_the_system_provides(
    client, auth, libs
):
    shop, lib = libs
    a = client.get("/api/groups/analysis", headers=auth, params={"repo_ids": f"{shop['id']},{lib['id']}"}).json()
    (link,) = [x for x in a["links"] if (x["src"], x["dst"]) == (shop["id"], lib["id"])]
    assert link["imports"] >= 5 and link["trust"] == "fact" and {"USES", "IMPORTS"} <= set(link["rels"])
    assert not [x for x in a["links"] if x["src"] == lib["id"]]  # the library imports nothing from the shop
    assert not any("requests" in e["evidence"] for e in link["examples"])


def test_documents_are_never_part_of_the_code_graph_even_with_name_matches_shown(client, auth, libs):
    shop, lib = libs
    q = {"repo_ids": f"{shop['id']},{lib['id']}", "weak": "true"}
    g = client.get("/api/groups/code-graph", headers=auth, params=q).json()
    assert {n["type"] for n in g["nodes"]} <= {"function", "class", "method", "file"}
    assert "MENTIONS" not in {e["rel"] for e in g["edges"]}
    # a call the import already proves is not drawn a second time as a name guess
    pairs = [(e["source"], e["target"]) for e in g["edges"] if e["rel"] in ("USES", "CALLS")]
    assert len(pairs) == len(set(pairs))
