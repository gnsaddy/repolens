import pytest

from repolens.services import ci_logs
from tests.test_api import add_repo

JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest" tests="3" failures="1" errors="1">
<testcase classname="tests.test_profile" name="test_deleted_user" file="tests/test_profile.py" line="42">
  <failure message="AttributeError: 'NoneType' object has no attribute 'email'">Traceback (most recent call last):
  File "app/user_profile.py", line 18, in get_profile
    return user.email
AttributeError: 'NoneType' object has no attribute 'email'</failure></testcase>
<testcase classname="tests.test_ok" name="test_fine"/>
<testcase classname="tests.test_db" name="test_connect"><error message="ConnectionError">timeout</error></testcase>
<testcase classname="tests.test_skip" name="test_skipped"><skipped/></testcase>
</testsuite></testsuites>"""

PYTEST = """============ test session starts ============
collected 2 items
tests/test_orders.py F.                                      [100%]
================ FAILURES =================
_______________ test_total_rounding _______________

    def test_total_rounding():
>       assert compute_total([{"price": 0.1, "qty": 3}], 0) == 0.3
E       assert 0.30000000000000004 == 0.3

tests/test_orders.py:12: AssertionError
=========== short test summary info ===========
FAILED tests/test_orders.py::test_total_rounding - assert 0.30000000000000004 == 0.3
"""

JEST = """ FAIL  src/cart.test.ts
  ● Cart › applies discount

    expect(received).toBe(expected)
    Expected: 90
    Received: 100

      at Object.<anonymous> (src/cart.test.ts:14:22)

Test Suites: 1 failed, 1 total
"""

GO = """=== RUN   TestParse
--- FAIL: TestParse (0.00s)
    parse_test.go:27: got 3, want 4
FAIL
"""

TRACE = """INFO starting worker
Traceback (most recent call last):
  File "/srv/app/worker.py", line 77, in run
    job.execute()
  File "/srv/app/jobs.py", line 31, in execute
    return self.payload["id"]
KeyError: 'id'
INFO done
"""


def test_junit_failures_errors_and_skips():
    fmt, fs = ci_logs.parse_ci_log(JUNIT)
    assert fmt == "junit" and [f.name for f in fs] == [
        "tests.test_profile.test_deleted_user",
        "tests.test_db.test_connect",
    ]
    assert (
        fs[0].file == "tests/test_profile.py"
        and fs[0].line == 42
        and "NoneType" in fs[0].message
        and "user_profile.py" in fs[0].details
    )


def test_junit_rejects_entity_tricks_and_bad_xml():
    with pytest.raises(ci_logs.CiLogError, match="DOCTYPE"):
        ci_logs.parse_ci_log('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><testsuite/>')
    with pytest.raises(ci_logs.CiLogError, match="valid XML"):
        ci_logs.parse_ci_log("<?xml version='1.0'?><testsuite><oops")


def test_pytest_sections_pick_message_and_frame():
    fmt, fs = ci_logs.parse_ci_log(PYTEST)
    assert fmt == "pytest" and len(fs) == 1
    f = fs[0]
    assert (
        f.name == "test_total_rounding"
        and "0.30000000000000004" in f.message
        and f.file == "tests/test_orders.py"
        and f.line == 12
    )


def test_pytest_summary_only_logs_still_work():
    fmt, fs = ci_logs.parse_ci_log("FAILED tests/test_a.py::test_x - KeyError: 'a'\nFAILED tests/test_a.py::test_y\n")
    assert (
        fmt == "pytest"
        and [f.name for f in fs] == ["tests/test_a.py::test_x", "tests/test_a.py::test_y"]
        and fs[0].file == "tests/test_a.py"
    )


def test_jest_go_and_plain_traces():
    fmt, fs = ci_logs.parse_ci_log(JEST)
    assert (
        fmt == "jest"
        and fs[0].name == "Cart › applies discount"
        and fs[0].file == "src/cart.test.ts"
        and fs[0].line == 14
    )
    fmt, fs = ci_logs.parse_ci_log(GO)
    assert fmt == "go" and fs[0].name == "TestParse" and fs[0].file == "parse_test.go" and fs[0].line == 27
    fmt, fs = ci_logs.parse_ci_log(TRACE)
    assert fmt == "trace" and fs[0].name == "KeyError" and fs[0].file == "/srv/app/jobs.py" and fs[0].line == 31


def test_ansi_is_stripped_duplicates_merge_and_limits_hold():
    fmt, fs = ci_logs.parse_ci_log("\x1b[31m" + TRACE + "\x1b[0m" + TRACE)
    assert len(fs) == 1 and fs[0].count == 2
    with pytest.raises(ci_logs.CiLogError, match="empty"):
        ci_logs.parse_ci_log("   \n")
    with pytest.raises(ci_logs.CiLogError, match="2 MB"):
        ci_logs.parse_ci_log("x" * (ci_logs.MAX_LOG + 1))
    assert ci_logs.parse_ci_log("all green, nothing failed")[1] == []


def test_secrets_in_logs_are_redacted_and_query_is_bounded():
    _, fs = ci_logs.parse_ci_log(
        'Traceback (most recent call last):\n  File "a.py", line 1, in f\n    x\nValueError: bad token ghp_'
        + "a" * 36
        + "\n"
    )
    f = ci_logs.sanitize(fs[0])
    assert "ghp_" not in f.message + f.details
    assert len(ci_logs.to_query(f)) < 12100 and ci_logs.to_query(f).startswith("CI test failure:")


def test_api_preview_and_start(client, auth, demo_repo):
    repo = add_repo(client, auth, demo_repo[0])
    r = client.post("/api/investigations/ci/parse", headers=auth, json={"log": JUNIT})
    assert (
        r.status_code == 200 and r.json()["format"] == "junit" and [f["index"] for f in r.json()["failures"]] == [0, 1]
    )
    assert (
        client.post(
            "/api/investigations/ci/parse", headers=auth, json={"log": '<?xml version="1.0"?><!DOCTYPE x><a/>'}
        ).status_code
        == 400
    )
    assert client.post("/api/investigations/ci/parse", json={"log": JUNIT}).status_code == 401

    log = "Traceback (most recent call last):\n  File \"app/user_profile.py\", line 18, in get_profile\n    return user.email\nAttributeError: 'NoneType' object has no attribute 'email'\n"
    s = client.post(
        "/api/investigations/ci/start", headers=auth, json={"log": log, "repo_ids": [repo["id"]], "indexes": [0]}
    )
    assert s.status_code == 202 and len(s.json()["started"]) == 1
    inv = client.get(f"/api/investigations/{s.json()['started'][0]['id']}", headers=auth).json()
    assert inv["status"] == "completed" and inv["title"].startswith("CI failure: AttributeError") and inv["hypotheses"]
    assert (
        client.post("/api/investigations/ci/start", headers=auth, json={"log": log, "indexes": [5]}).status_code == 400
    )
    assert (
        client.post(
            "/api/investigations/ci/start", headers=auth, json={"log": log, "repo_ids": ["nope"], "indexes": [0]}
        ).status_code
        == 404
    )
