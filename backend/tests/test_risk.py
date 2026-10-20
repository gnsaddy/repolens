import os
import subprocess
from pathlib import Path

import pytest

from repolens.services.risk import LIMITATIONS, MODEL_VERSION, WEIGHTS
from tests.conftest import register
from tests.test_api import add_repo
from tests.test_github import connect, github  # noqa: F401  (fixture)

ORDERS = """def compute_total(items, tax_rate):
    subtotal = sum(i["price"] * i["qty"] for i in items)
    return round(subtotal * (1 + tax_rate), 2)


def apply_discount(total, pct):
    return total * (1 - pct / 100)


def _internal_helper(x):
    return x
"""
FILES = {
    "shop/__init__.py": "",
    "shop/orders.py": ORDERS,
    "shop/checkout.py": "from .orders import apply_discount, compute_total\n\n\ndef checkout(cart):\n    total = compute_total(cart, 0.2)\n    return apply_discount(total, 5)\n",
    "shop/api/routes.py": "from ..checkout import checkout\n\n\ndef post_checkout(cart):\n    return checkout(cart)\n",
    "shop/auth/tokens.py": 'def issue_token(user):\n    return "t-" + user\n',
    "config/settings.yaml": "retries: 3\n",
    "tests/test_orders.py": 'from shop.orders import compute_total\n\n\ndef test_total():\n    assert compute_total([{"price": 2, "qty": 2}], 0) == 4\n',
    "docs/pricing.md": "# Pricing\n\nThe total comes from `compute_total`; discounts use `apply_discount`.\n",
}


def git(path: Path, *args, date: str | None = None):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "d",
        "GIT_AUTHOR_EMAIL": "d@x.io",
        "GIT_COMMITTER_NAME": "d",
        "GIT_COMMITTER_EMAIL": "d@x.io",
    }
    if date:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def commit(path: Path, files: dict, msg: str, date: str) -> str:
    for rel, content in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    git(path, "add", "-A")
    git(path, "commit", "-qm", msg, date=date)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def shop(tmp_path):
    p = tmp_path / "shop-repo"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    s = {"initial": commit(p, FILES, "feat: initial shop", "2026-01-05T10:00:00Z")}
    s["reports"] = commit(
        p,
        {
            "shop/reports.py": "from .orders import compute_total\n\n\ndef revenue(orders):\n    return sum(compute_total(o, 0) for o in orders)\n"
        },
        "feat: add reports",
        "2026-02-01T10:00:00Z",
    )
    cur = ORDERS.replace("round(subtotal * (1 + tax_rate), 2)", "round(subtotal * (1 + tax_rate), 2) if items else 0")
    s["fix1"] = commit(p, {"shop/orders.py": cur}, "fix: rounding bug in compute_total BUG-12", "2026-03-01T10:00:00Z")
    cur = cur.replace("pct / 100", "float(pct) / 100")
    s["fix2"] = commit(p, {"shop/orders.py": cur}, "fix: regression in apply_discount", "2026-04-01T10:00:00Z")
    s["docs"] = commit(
        p,
        {"docs/pricing.md": FILES["docs/pricing.md"] + "\nTaxes are added last.\n"},
        "docs: update pricing",
        "2026-05-01T10:00:00Z",
    )
    big = "\n".join(f"def rotate_{i}(user):\n    return user + '{i}'\n" for i in range(30))
    s["auth"] = commit(
        p,
        {"shop/auth/tokens.py": FILES["shop/auth/tokens.py"] + big, "config/settings.yaml": "retries: 5\n"},
        "feat: rotate tokens",
        "2026-05-10T10:00:00Z",
    )
    cur = cur.replace('sum(i["price"] * i["qty"] for i in items)', 'sum(float(i["price"]) * i["qty"] for i in items)')
    s["core"] = commit(p, {"shop/orders.py": cur}, "refactor: compute_total handles decimals", "2026-05-20T10:00:00Z")
    s["cur"] = cur
    return p, s


def assess(client, auth, repo, **kw):
    r = client.post("/api/risk/assess", headers=auth, json={"repo_id": repo["id"], **kw})
    assert r.status_code == 201, r.text
    return r.json()


def sig(a, key):
    return next(s for s in a["signals"] if s["key"] == key)


@pytest.fixture
def repo(client, auth, shop):
    return add_repo(client, auth, shop[0], name="shop")


def test_weights_and_points_add_up(client, auth, repo, shop):
    a = assess(client, auth, repo, sha=shop[1]["core"])
    assert sum(WEIGHTS.values()) == pytest.approx(1.0) and a["model_version"] == MODEL_VERSION
    assert a["score"] == pytest.approx(sum(s["points"] for s in a["signals"]), abs=0.2)
    assert {s["key"] for s in a["signals"]} == set(WEIGHTS)
    assert all(s["detail"] and 0 <= s["value"] <= 1 and s["points"] <= 100 * s["weight"] + 0.01 for s in a["signals"])
    assert a["limitations"] == LIMITATIONS and a["level"] in ("low", "medium", "high", "critical")


def test_docs_only_change_is_lowest(client, auth, repo, shop):
    docs = assess(client, auth, repo, sha=shop[1]["docs"])
    assert docs["score"] < 8 and docs["level"] == "low"
    assert sig(docs, "tests")["value"] == 0 and sig(docs, "sensitive")["value"] == 0


def test_untested_sensitive_change_scores_higher_with_reasons(client, auth, repo, shop):
    docs = assess(client, auth, repo, sha=shop[1]["docs"])
    auth_c = assess(client, auth, repo, sha=shop[1]["auth"])
    assert auth_c["score"] > docs["score"] + 15
    assert sig(auth_c, "sensitive")["value"] == 1.0 and "auth" in sig(auth_c, "sensitive")["detail"]
    assert sig(auth_c, "tests")["value"] == 1.0 and "0/1" in sig(auth_c, "tests")["detail"]
    assert {e["path"] for e in sig(auth_c, "sensitive")["evidence"]} >= {"shop/auth/tokens.py", "config/settings.yaml"}


def test_core_change_explains_blast_radius(client, auth, repo, shop):
    a = assess(client, auth, repo, sha=shop[1]["core"])
    aff = a["affected"]
    assert aff["symbol_level"] is True and [s["name"] for s in aff["symbols"]] == ["compute_total"]
    deps = {d["name"] for d in aff["dependents"]}
    assert {"checkout", "revenue"} <= deps  # callers in two different modules
    assert any(t["path"] == "tests/test_orders.py" for t in aff["tests_to_run"])
    assert any("Pricing" in d["name"] for d in aff["docs_to_update"])  # doc that mentions the symbol
    assert sig(a, "centrality")["value"] > 0.4 and sig(a, "tests")["value"] == 0  # covered by test_orders.py
    assert "dependents" in sig(a, "centrality")["detail"]


def test_history_uses_only_earlier_fixes(client, auth, repo, shop):
    core = assess(client, auth, repo, sha=shop[1]["core"])
    h = sig(core, "history")
    assert h["value"] > 0.2 and {e["sha"] for e in h["evidence"] if e["type"] == "fix_commit"} == {
        shop[1]["fix1"][:10],
        shop[1]["fix2"][:10],
    }
    # no look-ahead: when assessing the first fix, the later fix (and itself) must not count
    first_fix = assess(client, auth, repo, sha=shop[1]["fix1"])
    assert sig(first_fix, "history")["value"] == 0


def test_proposed_branch_range_before_merge(client, auth, repo, shop):
    path = shop[0]
    git(path, "checkout", "-q", "-b", "feature/discount")
    commit(
        path,
        {"shop/orders.py": shop[1]["cur"].replace("float(pct) / 100", "float(pct) / 100.0 if pct else 0")},
        "feat: discount edge case",
        "2026-06-01T10:00:00Z",
    )
    git(path, "checkout", "-q", "main")
    a = assess(client, auth, repo, base="main", head="feature/discount")
    assert (
        a["ref_type"] == "range"
        and a["affected"]["symbol_level"]
        and [s["name"] for s in a["affected"]["symbols"]] == ["apply_discount"]
    )
    assert {"checkout"} <= {d["name"] for d in a["affected"]["dependents"]}


def test_ref_injection_and_bad_refs_rejected(client, auth, repo):
    for bad in ("--output=/tmp/pwned", "-p", "main;rm -rf /", "../../etc"):
        r = client.post("/api/risk/assess", headers=auth, json={"repo_id": repo["id"], "base": bad, "head": "main"})
        assert r.status_code == 400, bad
    r = client.post(
        "/api/risk/assess", headers=auth, json={"repo_id": repo["id"], "base": "main", "head": "nope-branch"}
    )
    assert r.status_code == 400
    assert not Path("/tmp/pwned").exists()


def test_files_mode_is_file_level_and_validates_modes(client, auth, repo):
    a = assess(
        client,
        auth,
        repo,
        files=[
            {"path": "shop/orders.py", "additions": 300, "deletions": 120},
            {"path": "shop/auth/tokens.py", "additions": 10, "deletions": 0},
        ],
        title="Big change",
    )
    assert a["affected"]["symbol_level"] is False and a["title"] == "Big change" and a["ref_type"] == "files"
    assert sig(a, "size")["value"] > 0.5 and sig(a, "sensitive")["value"] == 1.0
    assert client.post("/api/risk/assess", headers=auth, json={"repo_id": repo["id"]}).status_code == 422
    assert (
        client.post(
            "/api/risk/assess", headers=auth, json={"repo_id": repo["id"], "sha": "abcd", "pr_number": 1}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/risk/assess", headers=auth, json={"repo_id": repo["id"], "sha": "deadbeef"}).status_code
        == 404
    )


def test_scan_ranks_recent_commits(client, auth, repo, shop):
    ranked = client.get("/api/risk/scan", headers=auth, params={"repo_id": repo["id"], "limit": 10}).json()
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True) and len(ranked) == 7
    assert ranked[-1]["sha"] in (shop[1]["docs"], shop[1]["initial"], shop[1]["reports"]) or ranked[-1]["score"] < 10
    assert next(r for r in ranked if r["sha"] == shop[1]["docs"])["level"] == "low"


def test_persisted_list_get_and_isolation(client, auth, repo, shop):
    a = assess(client, auth, repo, sha=shop[1]["core"])
    lst = client.get("/api/risk", headers=auth, params={"repo_id": repo["id"]}).json()
    assert lst[0]["id"] == a["id"] and "signals" not in lst[0] and lst[0]["top_signals"]
    assert client.get(f"/api/risk/{a['id']}", headers=auth).json()["signals"]
    other = register(client, "x@other.io", "Other")
    assert client.get(f"/api/risk/{a['id']}", headers=other).status_code == 404
    assert (
        client.post("/api/risk/assess", headers=other, json={"repo_id": repo["id"], "sha": shop[1]["core"]}).status_code
        == 404
    )
    client.post(
        "/api/auth/users", headers=auth, json={"email": "v@acme.io", "password": "viewer-pass-1", "role": "viewer"}
    )
    tok = client.post("/api/auth/login", json={"email": "v@acme.io", "password": "viewer-pass-1"}).json()[
        "access_token"
    ]
    assert (
        client.post(
            "/api/risk/assess",
            headers={"Authorization": f"Bearer {tok}"},
            json={"repo_id": repo["id"], "sha": shop[1]["core"]},
        ).status_code
        == 403
    )
    assert client.get(f"/api/risk/{a['id']}", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_cross_repository_dependents(client, auth, repo, shop, tmp_path):
    other = tmp_path / "billing"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    commit(
        other,
        {"billing/invoice.py": "def make_invoice(cart):\n    return compute_total(cart, 0.1)\n"},
        "feat: invoices",
        "2026-02-10T10:00:00Z",
    )
    add_repo(client, auth, other, name="billing")
    client.post(f"/api/repositories/{repo['id']}/index", headers=auth)  # re-link cross-repo edges from the new repo
    a = assess(client, auth, repo, sha=shop[1]["core"])
    cross = [d for d in a["affected"]["dependents"] if d["cross_repo"]]
    assert cross and cross[0]["repo"] == "billing" and cross[0]["name"] == "make_invoice"
    assert "other repositories" in sig(a, "centrality")["detail"]


def test_pr_assessment_needs_synced_pr_and_webhook_auto_assesses(client, auth, repo, github, shop, monkeypatch):  # noqa: F811
    import hashlib
    import hmac
    import json

    from repolens.config import get_settings

    assert (
        client.post("/api/risk/assess", headers=auth, json={"repo_id": repo["id"], "pr_number": 7}).status_code == 404
    )
    github["prs"] = {
        7: {
            "number": 7,
            "title": "Rework compute_total",
            "state": "closed",
            "merged_at": "2026-05-21T10:00:00Z",
            "created_at": "2026-05-19T10:00:00Z",
            "html_url": "https://github.com/acme/user-service/pull/7",
            "user": {"login": "d"},
            "base": {"ref": "main"},
            "head": {"ref": "x"},
            "merge_commit_sha": shop[1]["core"],
            "labels": [],
            "body": "Touches `compute_total`.",
        }
    }
    connect(client, auth)
    r = client.post(f"/api/repositories/{repo['id']}/sync-prs", headers=auth, json={"github_repo": "acme/user-service"})
    assert r.status_code == 202
    a = assess(client, auth, repo, pr_number=7)
    assert a["ref_type"] == "pr" and a["ref"] == "#7" and a["affected"]["symbol_level"] is False
    # a webhook-triggered sync stores an assessment without anyone asking
    monkeypatch.setenv("REPOLENS_GITHUB_WEBHOOK_SECRET", "whsec")
    get_settings.cache_clear()
    before = len(client.get("/api/risk", headers=auth, params={"repo_id": repo["id"]}).json())
    client.put("/api/repositories", headers=auth) if False else None
    gitrepo = client.post(
        "/api/repositories",
        headers=auth,
        json={"name": "shop-git", "source": "git", "url": "https://github.com/acme/user-service.git"},
    ).json()
    body = json.dumps(
        {
            "action": "opened",
            "pull_request": {"number": 7},
            "repository": {"clone_url": "https://github.com/acme/user-service.git", "full_name": "acme/user-service"},
        }
    ).encode()
    sig_ = "sha256=" + hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    out = client.post(
        "/api/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sig_, "Content-Type": "application/json"},
    ).json()
    assert out["pr_synced"] == [gitrepo["id"]]
    assert len(client.get("/api/risk", headers=auth, params={"repo_id": gitrepo["id"]}).json()) == 1 and before >= 1
