import json

from repolens.models import Change, CodeChunk, Document
from repolens.security import redact_stored
from repolens.services.github import normalize_pull
from repolens.services.parsing import detect_language, should_index
from tests.conftest import register
from tests.test_api import add_repo
from tests.test_risk import commit, git

AWS = "AKIAIOSFODNN7EXAMPLE"
SK = "sk-live-abcdefghijklmnopqrstuvwx"
GHP = "ghp_" + "Z" * 36


# ------------------------------------------------------------------ redaction unit tests
def test_redacts_high_confidence_secrets():
    text = f'api_key = "{SK}"\nAWS = {AWS}\nDB_PASSWORD = "hunter2hunter2hunter2"\nu = "postgres://admin:s3cretpass@db/x"\nt = {GHP}\n'
    out, n = redact_stored(text)
    assert n == 5 and not any(s in out for s in (SK, AWS, "hunter2", "s3cretpass", GHP))
    assert (
        'DB_PASSWORD = "[REDACTED]"' in out and "postgres://admin:[REDACTED]@db/x" in out
    )  # names and structure survive


def test_redaction_leaves_ordinary_code_alone():
    code = (
        'token = get_token()\npassword_hash = hash(pw)\nPW = os.environ["PASSWORD"]\nsha = "3665ca7b3665ca7b3665ca7b"\n'
        'secret_name = "short"\nclass TokenService:\n    def refresh_token(self, token):\n        return token\n'
    )
    assert redact_stored(code) == (code, 0)


def test_private_key_blocks_are_removed():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nabc\n-----END RSA PRIVATE KEY-----"
    out, n = redact_stored(f"before\n{pem}\nafter")
    assert n == 1 and "MIIEow" not in out and out.startswith("before") and out.endswith("after")


def test_pr_text_is_redacted_on_ingest():
    pr = normalize_pull(
        {"number": 1, "title": "t", "body": f"deploy with {GHP}", "created_at": "2026-01-01T00:00:00Z"},
        [],
        [],
        [{"user": {"login": "r"}, "body": f"key {AWS} leaked", "path": ""}],
    )
    assert GHP not in pr["body"] and AWS not in json.dumps(pr["comments"])


# ------------------------------------------------------------------ file selection
def test_config_and_deployment_files_are_selected():
    for ok in (
        "Dockerfile",
        "svc/Dockerfile.prod",
        "deploy/nginx.conf",
        "infra/main.tf",
        "app/settings.ini",
        "Makefile",
        "Jenkinsfile",
        ".env.example",
        "config.properties",
        "k8s/deploy.yaml",
        "ops/app.service.j2",
    ):
        assert should_index(ok, 100, 10_000), ok
    assert detect_language("Dockerfile") == "config" and detect_language("infra/main.tf") == "config"
    for bad in (
        ".env",
        ".env.production",
        "certs/server.key",
        "certs/ca.pem",
        "node_modules/x/Dockerfile",
        "keystore.jks",
    ):
        assert not should_index(bad, 100, 10_000), bad


# ------------------------------------------------------------------ end to end
def make_infra_repo(tmp_path):
    p = tmp_path / "gateway"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(
        p,
        {
            "Dockerfile": "FROM python:3.12\nCOPY . /app\n",
            "deploy/tls.conf": "ssl_protocols TLSv1.2 TLSv1.3;\nssl_ciphers HIGH:!aNULL;\n",
            "app/main.py": "def serve():\n    return 1\n",
            ".env": f"SECRET_KEY={SK}\n",
            "certs/server.key": "-----BEGIN PRIVATE KEY-----\nAAA\n-----END PRIVATE KEY-----\n",
            ".env.example": "SECRET_KEY=changeme\n",
        },
        "feat: gateway",
        "2026-01-05T10:00:00Z",
    )
    commit(
        p,
        {
            "deploy/tls.conf": "ssl_protocols TLSv1.3;\nssl_ciphers HIGH:!aNULL;\n",
            "app/settings.py": f'API_KEY = "{SK}"\nAWS = "{AWS}"\nREGION = "eu"\n',
        },
        "chore: tighten tls policy",
        "2026-02-01T10:00:00Z",
    )
    return p


def test_config_files_are_searchable_and_secret_files_stay_out(client, auth, tmp_path, session):
    repo = add_repo(client, auth, make_infra_repo(tmp_path), name="gateway")
    paths = {t["path"] for t in client.get(f"/api/repositories/{repo['id']}/tree", headers=auth).json()}
    assert {"Dockerfile", "deploy/tls.conf", ".env.example", "app/main.py"} <= paths
    assert ".env" not in paths and "certs/server.key" not in paths
    hits = client.get(
        "/api/search", headers=auth, params={"q": "ssl_protocols TLSv1.3 policy", "repo_id": repo["id"]}
    ).json()
    assert hits[0]["file"] == "deploy/tls.conf"
    assert session.query(CodeChunk).filter(CodeChunk.file_path == "Dockerfile").one().language == "config"


def test_secrets_are_redacted_everywhere_they_are_stored_or_shown(client, auth, tmp_path, session):
    repo = add_repo(client, auth, make_infra_repo(tmp_path), name="gateway")
    stored = " ".join(
        [c.content for c in session.query(CodeChunk)] + [c.diff_excerpt + c.message for c in session.query(Change)]
    )
    assert SK not in stored and AWS not in stored and "[REDACTED]" in stored
    run = client.get(f"/api/repositories/{repo['id']}/runs", headers=auth).json()[0]
    assert run["secrets_redacted"] >= 3  # two in the chunk, plus the diff that added them
    view = client.get(f"/api/repositories/{repo['id']}/file", headers=auth, params={"path": "app/settings.py"}).json()
    assert SK not in view["content"] and AWS not in view["content"] and 'REGION = "eu"' in view["content"]
    assert client.get("/api/search", headers=auth, params={"q": SK}).json() == [] or all(
        SK not in h["snippet"] for h in client.get("/api/search", headers=auth, params={"q": SK}).json()
    )


def test_uploaded_documents_are_redacted_and_reported(client, auth, session):
    col = client.post("/api/knowledge/collections", headers=auth, json={"name": "runbooks"}).json()
    body = {"documents": [{"path": "ops.md", "content": f'# Ops\n\nExport `API_TOKEN = "{SK}"` before deploy.\n'}]}
    r = client.post(f"/api/knowledge/collections/{col['id']}/documents", headers=auth, json=body).json()
    assert r["secrets_redacted"] == 1
    assert SK not in session.query(Document).one().content
    assert SK not in " ".join(c.content for c in session.query(CodeChunk))
    f = client.get(f"/api/repositories/{col['id']}/file", headers=auth, params={"path": "ops.md"}).json()
    assert "[REDACTED]" in f["content"] and SK not in f["content"]
    assert register(client, "o@other.io", "Other")
