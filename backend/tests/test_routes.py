from repolens.services import routes
from tests.test_api import add_repo
from tests.test_chat import new_chat, say

FASTAPI = """from fastapi import APIRouter
router = APIRouter(prefix="/api/wiki", tags=["wiki"])

@router.get("/overview")
def overview(p):
    return 1

@router.post("/ask", status_code=201)
async def ask(body):
    return 2
"""


def test_fastapi_routes_get_their_prefix_method_and_handler():
    got = routes.extract("api/wiki.py", FASTAPI)
    assert [(r.method, r.path, r.handler) for r in got] == [
        ("GET", "/api/wiki/overview", "overview"),
        ("POST", "/api/wiki/ask", "ask"),
    ]
    assert routes.parse(routes.chunks("api/wiki.py", FASTAPI)[0].content) == got  # written and read back unchanged


def test_flask_express_spring_and_go_routes():
    flask = '@app.route("/health", methods=["GET", "POST"])\ndef health():\n    pass\n'
    assert [(r.method, r.path) for r in routes.extract("app.py", flask)] == [("GET", "/health"), ("POST", "/health")]
    express = "const r = 1;\nrouter.post('/users/:id', handler);\naxios.get('/api/x');\n"
    assert [(r.method, r.path) for r in routes.extract("srv.js", express)] == [
        ("POST", "/users/:id")
    ]  # axios is a client
    spring = '@GetMapping("/orders")\npublic List<Order> list() {\n}\n'
    assert [(r.method, r.path, r.handler) for r in routes.extract("A.java", spring)] == [("GET", "/orders", "list")]
    assert [(r.method, r.path) for r in routes.extract("m.go", 'r.GET("/ping", h)\n')] == [("GET", "/ping")]


def test_tests_docs_and_other_files_are_ignored():
    assert routes.extract("tests/test_api.py", FASTAPI) == []
    assert routes.extract("README.md", FASTAPI) == []
    assert routes.chunks("x.py", "print(1)") == []


def test_listing_all_endpoints_is_exact_and_cited(client, auth, tmp_path, monkeypatch):
    import subprocess

    d = tmp_path / "svc"
    d.mkdir()
    (d / "api.py").write_text(FASTAPI + "\n@router.delete('/page/{id}')\ndef delete_page(id):\n    pass\n")
    (d / "other.py").write_text('@app.get("/healthz")\ndef healthz():\n    pass\n')
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(d), *cmd], check=True)
    repo = add_repo(client, auth, str(d), name="svc")
    c = new_chat(client, auth, repo_ids=[repo["id"]])
    r = say(client, auth, c["id"], "List all the API endpoints in this service")
    assert r.status_code == 201
    text = r.json()["items"][-1]["content"]
    for want in ("GET /api/wiki/overview", "POST /api/wiki/ask", "DELETE /api/wiki/page/{id}", "GET /healthz"):
        assert want in text
    assert "Found **4** endpoints in 2 files" in text and "[S1]" in text and "[S2]" in text
    got = r.json()["items"][-1]
    assert {s["file"] for s in got["sources"]} == {"api.py", "other.py"}


def test_questions_that_are_not_list_everything_still_search():
    from repolens.services.chat import wants_all_routes as w

    assert w("List all the API endpoint in repolens") and w("show me every route") and w("what are all the endpoints?")
    assert not w("how does the login endpoint work?") and not w("list all the files") and not w("what is a route")


def test_without_an_inventory_the_answer_says_to_reindex(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])  # a repository without route declarations
    c = new_chat(client, auth, repo_ids=[repo["id"]])
    text = say(client, auth, c["id"], "List all the API endpoints").json()["items"][-1]["content"]
    assert "Re-index" in text
