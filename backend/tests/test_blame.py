from repolens.demo import BUG_REPORT
from repolens.services import gitops
from tests.conftest import register
from tests.test_api import add_repo
from tests.test_risk import shop  # noqa: F401  (fixture)


def test_blame_range_attributes_lines_to_the_commits_that_wrote_them(shop):  # noqa: F811
    path, shas = shop
    head = gitops.head_sha(str(path))
    counts = gitops.blame_range(str(path), head, "shop/orders.py", 1, 4)  # compute_total after three edits
    assert sum(counts.values()) == 4 and shas["initial"] in counts
    assert shas["core"] in counts and counts[shas["core"]] == 1  # the decimal refactor rewrote exactly one line
    assert gitops.blame_range(str(path), head, "shop/nope.py", 1, 3) == {}
    assert gitops.blame_range(str(path), head, "shop/orders.py", 5, 2) == {}  # reversed range
    assert gitops.blame_range(str(path), head, "shop/orders.py", 1, 99999) == {}  # absurd range


def test_blame_endpoint(client, auth, shop):  # noqa: F811
    path, shas = shop
    repo = add_repo(client, auth, path, name="shop")
    rows = client.get(
        f"/api/repositories/{repo['id']}/blame", headers=auth, params={"path": "shop/orders.py", "start": 1, "end": 4}
    ).json()
    assert sum(r["lines"] for r in rows) == 4 and abs(sum(r["share"] for r in rows) - 1) < 0.01
    core = next(r for r in rows if r["sha"] == shas["core"])
    assert core["lines"] == 1 and "decimals" in core["message"] and core["author"] == "d"
    assert rows == sorted(rows, key=lambda r: -r["lines"])
    for bad in ("../../etc/passwd", "shop/not_indexed.py", ".git/config"):
        assert (
            client.get(f"/api/repositories/{repo['id']}/blame", headers=auth, params={"path": bad}).status_code == 404
        )
    other = register(client, "x@other.io", "Other")
    assert (
        client.get(
            f"/api/repositories/{repo['id']}/blame", headers=other, params={"path": "shop/orders.py"}
        ).status_code
        == 404
    )


def test_blame_of_the_failing_line_finds_a_replacement_bug(client, auth, tmp_path):
    from tests.test_risk import commit, git

    p = tmp_path / "calc"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    base = {
        "calc/__init__.py": "",
        "calc/ops.py": "def ratio(a, b):\n    return a / b if b else 0\n\n\ndef double(x):\n    return x * 2\n",
    }
    commit(p, base, "feat: calc", "2026-01-05T10:00:00Z")
    commit(
        p,
        {"calc/ops.py": base["calc/ops.py"] + "\n\ndef triple(x):\n    return x * 3\n"},
        "feat: triple",
        "2026-02-01T10:00:00Z",
    )
    bug = commit(
        p,
        {
            "calc/ops.py": base["calc/ops.py"].replace("a / b if b else 0", "a / b")
            + "\n\ndef triple(x):\n    return x * 3\n"
        },
        "refactor: simplify ratio",
        "2026-03-01T10:00:00Z",
    )
    commit(p, {"README.md": "# calc\n"}, "docs: readme", "2026-03-05T10:00:00Z")
    repo = add_repo(client, auth, p, name="calc")
    report = (
        'Division by zero in ratio.\n\nTraceback (most recent call last):\n  File "calc/ops.py", line 2, in ratio\n    return a / b\n'
        "ZeroDivisionError: division by zero\n"
    )
    inv = client.post("/api/investigations", headers=auth, json={"query": report, "repo_ids": [repo["id"]]}).json()
    inv = client.get(f"/api/investigations/{inv['id']}", headers=auth).json()
    commit_ev = next(e for e in inv["hypotheses"][0]["evidence"] if e["type"] == "commit")
    assert commit_ev["sha"] == bug and commit_ev["blame_share"] == 1.0  # the line was written by the regression commit
    assert "git blame: last wrote the failing line" in inv["report_md"]


def test_deleted_line_regressions_are_found_without_claiming_blame(client, auth, demo_repo):
    """The demo regression deletes a null guard. Blame only sees lines that still exist (the classic SZZ limit), so it
    must stay silent rather than blame the commit that originally wrote the surviving line."""
    path, bad_sha = demo_repo
    repo = add_repo(client, auth, path)
    inv = client.post("/api/investigations", headers=auth, json={"query": BUG_REPORT, "repo_ids": [repo["id"]]}).json()
    inv = client.get(f"/api/investigations/{inv['id']}", headers=auth).json()
    commit_ev = next(e for e in inv["hypotheses"][0]["evidence"] if e["type"] == "commit")
    assert commit_ev["sha"] == bad_sha  # still found via the commit's changed lines
    assert not commit_ev.get("blame_share") and "git blame" not in inv["report_md"]


def test_no_traceback_means_no_blame_claim(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    q = "profile display name missing for deleted users after the refactor"
    inv = client.post("/api/investigations", headers=auth, json={"query": q, "repo_ids": [repo["id"]]}).json()
    inv = client.get(f"/api/investigations/{inv['id']}", headers=auth).json()
    assert all(not e.get("blame_share") for h in inv["hypotheses"] for e in h["evidence"] if e["type"] == "commit")
