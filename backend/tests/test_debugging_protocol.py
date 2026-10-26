"""The report follows the debugging discipline: reproduce, localise, reduce, fix the cause, guard, verify, and an independent
second opinion that is never shown the conclusion it is checking."""

from repolens.agents import workflow as wf
from repolens.services.llm import LLMClient, LLMResult
from tests.test_api import add_repo, run_investigation

CODE = {
    "type": "code",
    "file": "src/user_profile.py",
    "symbol": "get_profile",
    "start_line": 9,
    "end_line": 14,
    "snippet": "x",
}
COMMIT = {"type": "commit", "sha": "a" * 40, "message": "refactor: tidy", "author": "a", "diff_excerpt": "-guard"}
TEST = {"type": "test", "file": "tests/test_user_profile.py"}


def hyp(rank=1, conf=0.8, evidence=(CODE, COMMIT, TEST), title="user_profile.py"):
    return {"rank": rank, "title": title, "confidence": conf, "explanation": "", "evidence": list(evidence)}


def test_a_complete_investigation_has_few_red_flags():
    assert wf.red_flags({"hypotheses": [hyp()], "stack_refs": [{"file": "x"}]}) == []


def test_red_flags_name_each_reason_not_to_trust_the_lead():
    flags = wf.red_flags(
        {"hypotheses": [hyp(conf=0.4, evidence=(CODE,)), hyp(2, 0.38, title="other.py")], "stack_refs": []}
    )
    text = " ".join(flags)
    for want in ("No stack trace", "No commit", "No test", "almost as likely", "Confidence is low"):
        assert want in text


def test_the_checklist_is_filled_in_with_what_was_found():
    steps = wf.debugging_checklist({"hypotheses": [hyp()], "stack_refs": [{"file": "x"}]})
    assert [s[:2] for s in steps] == ["1.", "2.", "3.", "4.", "5.", "6."]
    assert (
        "same error" in steps[0]
        and "`src/user_profile.py`" in steps[1]
        and "git bisect" in steps[1]
        and "aaaaaaaa" in steps[1]
    )
    assert "tests/test_user_profile.py" in steps[4]
    bare = wf.debugging_checklist({"hypotheses": [hyp(evidence=(CODE,))], "stack_refs": []})
    assert "stays a guess" in bare[0] and "no existing test" in bare[4]


def test_the_reviewer_is_not_shown_the_conclusion():
    shown = wf._evidence_only([hyp(title="THE-LEAD-TITLE", conf=0.91)])
    assert "THE-LEAD-TITLE" not in shown and "0.91" not in shown and "confidence" not in shown.lower()
    assert "src/user_profile.py" in shown and "refactor: tidy" in shown


def test_the_review_agrees_only_when_it_points_at_the_same_code():
    same = wf.reconcile(
        hyp(), {"likely_cause": "get_profile dereferences a missing key", "alternatives": ["a", "b", "c", "d"]}
    )
    assert same["agrees"] and len(same["alternatives"]) == 3
    other = wf.reconcile(hyp(), {"likely_cause": "the cache layer returns stale rows", "assumptions": "not a list"})
    assert not other["agrees"] and other["assumptions"] == []


def test_offline_report_includes_the_checklist_and_says_why_no_review_ran(client, auth, demo_repo):
    inv = run_investigation(client, auth, add_repo(client, auth, demo_repo[0])["id"])
    md = inv["report_md"]
    assert "### How to confirm it (debugging checklist)" in md and "**Reproduce:**" in md and "**Guard:**" in md
    assert "Independent review: not run (needs a connected language model)" in md
    assert inv["verification"]["review"]["run"] is False


def test_with_a_model_a_second_pass_is_made_and_reported(client, auth, demo_repo, monkeypatch):
    prompts = []

    def complete(this, agent, system, prompt, max_tokens=1500, task=None, validate=None):
        prompts.append((agent, prompt))
        if agent == "reviewer":
            return LLMResult(
                '{"likely_cause": "get_profile in user_profile.py reads a missing key", "alternatives": ["stale cache"], '
                '"assumptions": ["metadata is optional"], "check": "call get_profile with no metadata"}',
                5,
                5,
                route={
                    "model": "spy",
                    "tier": "light",
                    "escalated": False,
                    "provider": "spy",
                    "why": [],
                    "fallbacks": 0,
                },
            )
        raise RuntimeError("synthesis down")  # the investigation itself falls back to its deterministic narrative

    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", complete)
    monkeypatch.setattr("repolens.services.llm_router.savings_mode", lambda db, org: False)
    inv = run_investigation(client, auth, add_repo(client, auth, demo_repo[0])["id"])
    assert inv["status"] == "completed"
    md = inv["report_md"]
    assert "### Independent review" in md and "Quickest check: call get_profile with no metadata" in md
    assert "Also fits: stale cache" in md and inv["verification"]["review"]["run"] is True
    sent = next(p for a, p in prompts if a == "reviewer")
    assert "confidence" not in sent.lower() and "Hypothesis" not in sent  # evidence only, no conclusion


def test_a_failing_review_is_reported_not_hidden(client, auth, demo_repo, monkeypatch):
    def complete(this, agent, system, prompt, max_tokens=1500, task=None, validate=None):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", complete)
    monkeypatch.setattr("repolens.services.llm_router.savings_mode", lambda db, org: False)
    inv = run_investigation(client, auth, add_repo(client, auth, demo_repo[0])["id"])
    assert inv["status"] == "completed"
    assert "Independent review: not run (the review call failed (quota exceeded))" in inv["report_md"]


def test_savings_mode_skips_the_review_when_confidence_is_already_high_and_says_so(
    client, auth, demo_repo, monkeypatch
):
    calls = []

    def complete(this, agent, *a, **k):
        calls.append(agent)
        raise RuntimeError("down")

    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", complete)
    monkeypatch.setattr("repolens.services.llm_router.savings_mode", lambda db, org: True)
    inv = run_investigation(client, auth, add_repo(client, auth, demo_repo[0])["id"])
    assert "reviewer" not in calls
    assert "skipped in savings mode because confidence is already high" in inv["report_md"]


def test_a_differing_review_lowers_confidence_marks_it_unverified_and_can_change_the_lead():
    lead, runner = hyp(conf=0.8), hyp(2, 0.7, title="other.py")
    lead["status"] = "supported"
    review = {"run": True, "agrees": False}
    out = wf.apply_dissent([lead, runner], review)
    assert [h["title"] for h in out] == ["other.py", "user_profile.py"]  # 0.7 now beats 0.64
    assert [h["rank"] for h in out] == [1, 2]
    dropped = next(h for h in out if h["title"] == "user_profile.py")
    assert dropped["confidence"] == 0.64 and dropped["status"] == "unverified"
    assert review["confidence_before"] == 0.8 and review["confidence_after"] == 0.64
    assert lead["confidence"] == 0.8  # the original is left alone
    assert wf.apply_dissent([hyp(conf=0.04)], {})[0]["confidence"] == wf.CONFIDENCE_FLOOR


def test_an_agreeing_review_never_raises_confidence(client, auth, demo_repo, monkeypatch):
    def complete(this, agent, system, prompt, max_tokens=1500, task=None, validate=None):
        if agent == "reviewer":
            return LLMResult('{"likely_cause": "get_profile in user_profile.py", "check": "x"}', 5, 5)
        raise RuntimeError("synthesis down")

    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", complete)
    monkeypatch.setattr("repolens.services.llm_router.savings_mode", lambda db, org: False)
    base = run_investigation(client, auth, add_repo(client, auth, demo_repo[0], name="a")["id"])
    assert base["verification"]["review"]["agrees"] is True
    monkeypatch.undo()
    offline = run_investigation(client, auth, add_repo(client, auth, demo_repo[0], name="b")["id"])
    assert base["confidence"] == offline["confidence"]


def test_a_differing_review_changes_the_stored_confidence_and_the_report(client, auth, demo_repo, monkeypatch):
    def complete(this, agent, system, prompt, max_tokens=1500, task=None, validate=None):
        if agent == "reviewer":
            return LLMResult('{"likely_cause": "the cache layer returns stale rows", "alternatives": ["race"]}', 5, 5)
        raise RuntimeError("synthesis down")

    offline = run_investigation(client, auth, add_repo(client, auth, demo_repo[0], name="a")["id"])
    monkeypatch.setattr(LLMClient, "offline", property(lambda self: False))
    monkeypatch.setattr(LLMClient, "complete", complete)
    monkeypatch.setattr("repolens.services.llm_router.savings_mode", lambda db, org: False)
    inv = run_investigation(client, auth, add_repo(client, auth, demo_repo[0], name="b")["id"])
    assert inv["verification"]["review"]["agrees"] is False
    rv = inv["verification"]["review"]
    assert rv["confidence_after"] == round(rv["confidence_before"] * 0.8, 3)
    assert inv["confidence"] == max(h["confidence"] for h in inv["hypotheses"])  # the lead may now be the runner-up
    assert inv["confidence"] < offline["confidence"]
    assert any(
        h["status"] == "unverified" and abs(h["confidence"] - rv["confidence_after"]) < 0.01 for h in inv["hypotheses"]
    )
    assert "confidence was lowered from" in inv["report_md"] and "**Differs:**" in inv["report_md"]
