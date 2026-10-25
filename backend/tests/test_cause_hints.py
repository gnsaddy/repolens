from repolens.services.cause_hints import hint

READ = {
    "type": "code",
    "file": "src/faculty.py",
    "start_line": 8,
    "snippet": '    members = self.repo.find_by_department(d)\n    return [{"name": m["name"], "email": m["email"]} for m in members]',
}
BUILD = {
    "type": "code",
    "file": "src/repo.py",
    "start_line": 3,
    "snippet": '    return [{"email": "a@x.edu"}] if d == "deleted" else []',
}


def test_keyerror_points_at_the_unguarded_read_and_the_producer_missing_the_key():
    h = hint("Traceback ...\nKeyError: 'name'", [READ, BUILD])
    assert h and h["error"] == "KeyError" and h["detail"] == "name"
    assert "src/faculty.py:9" in h["summary"] and '`.get("name")`' in h["fix"]
    assert "src/repo.py:3" in h["explanation"] and "without `name`" in h["explanation"]
    assert [s["file"] for s in h["sites"]] == ["src/faculty.py", "src/repo.py"]


def test_none_attribute_and_js_undefined_reads():
    ev = [
        {
            "type": "code",
            "file": "a.py",
            "start_line": 1,
            "snippet": "user = find(uid)\nreturn user.email\n# or: return None",
        }
    ]
    h = hint("AttributeError: 'NoneType' object has no attribute 'email'", ev)
    assert h and h["detail"] == "email" and "a.py:2" in h["summary"]
    js = [{"type": "code", "file": "a.js", "start_line": 5, "snippet": "const n = row.profile.name;"}]
    assert hint("TypeError: Cannot read properties of undefined (reading 'name')", js)["detail"] == "name"


def test_other_errors_and_no_match():
    assert (
        hint(
            "IndexError: list index out of range",
            [{"type": "code", "file": "b.py", "start_line": 1, "snippet": "x = items[0]"}],
        )["error"]
        == "IndexError"
    )
    assert (
        hint(
            "ZeroDivisionError: division by zero",
            [{"type": "code", "file": "c.py", "start_line": 1, "snippet": "avg = total / count"}],
        )["error"]
        == "ZeroDivisionError"
    )
    assert hint("KeyError: 'name'", [{"type": "commit", "sha": "abc"}]) is None  # no code evidence
    assert hint("the page is slow", [READ]) is None
    assert hint("KeyError: 'zzz'", [READ]) is None  # nothing reads that key
