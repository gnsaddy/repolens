import subprocess

from repolens.evals.suites import bugs


def git(path, *args):
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=a", "-c", "user.email=a@b.c", *args], check=True, capture_output=True
    )


def commit(path, files, msg):
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-qm", msg)


def make_repo(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    git(tmp_path, "init", "-q", "-b", "main")
    commit(
        tmp_path,
        {"src/pay.py": "def charge(a):\n    return a\n", "tests/test_pay.py": "x=1\n", "README.md": "hi\n"},
        "Add payments",
    )
    commit(
        tmp_path,
        {"src/pay.py": "def charge(a):\n    return a or 0\n"},
        "Fix charge crashing on empty amount in src/pay.py",
    )
    commit(tmp_path, {"README.md": "hello\n"}, "Fix typo")  # docs only: no usable answer
    commit(tmp_path, {"src/new.py": "x = 1\n"}, "Fix: add a new module")  # only an added file: no usable answer
    return tmp_path


def test_mining_keeps_only_fixes_that_modified_source_and_masks_the_answer(tmp_path):
    cases = bugs.mine(str(make_repo(tmp_path)))
    assert [c["subject"] for c in cases] == ["Fix charge crashing on empty amount in src/pay.py"]
    c = cases[0]
    assert c["truth"] == ["src/pay.py"]
    assert "src/pay.py" not in c["query"] and "<file>" in c["query"]  # the report may not give the answer away
    assert c["parent"]


def test_the_snapshot_cannot_see_the_fix_or_anything_later(tmp_path):
    src = make_repo(tmp_path / "src")
    c = bugs.mine(str(src))[0]
    dest = tmp_path / "snap"
    bugs._snapshot(str(src), c["parent"], str(dest))
    log = subprocess.run(
        ["git", "-C", str(dest), "log", "--format=%s"], capture_output=True, text=True, check=True
    ).stdout
    assert log.strip() == "Add payments"
    shown = subprocess.run(
        ["git", "-C", str(dest), "show", "HEAD:src/pay.py"], capture_output=True, text=True, check=True
    ).stdout
    assert "or 0" not in shown  # the repaired line is not in the index
