from repolens.services import doc_intel as di
from repolens.services.docs import MAX_SECTION_LINES, WINDOW, parse_markdown

RUNBOOK = """# Restart the payments API

Use this runbook when the payments API stops answering health checks. Owner: platform-team

Owner: platform-team
On-call: @payments-oncall

> **Warning:** Never restart during a settlement window (02:00-03:00 UTC).

## Steps

1. Check health: `GET /healthz`
2. Restart:

```bash
$ kubectl rollout restart deploy/payments
kubectl get pods -l app=payments
```

Set `PAYMENTS_DB_URL` and `RETRY_LIMIT` if the pod crash-loops. Last reviewed 2026-09-30.
"""


def test_documents_are_classified_like_an_engineer_would():
    assert di.classify("ops/runbooks/restart-api.md", "Restart the payments API", RUNBOOK) == "runbook"
    assert (
        di.classify(
            "docs/adr/0007-use-postgres.md",
            "ADR 7: Use Postgres",
            "Status: accepted\n## Consequences\n## Alternatives considered",
        )
        == "decision"
    )
    assert (
        di.classify(
            "docs/api.md", "Payments API reference", "GET /v1/charges\nPOST /v1/refunds\nstatus code 404 response"
        )
        == "api"
    )
    assert di.classify("CHANGELOG.md", "Changelog", "## [1.2.0]\n- fixed") == "changelog"
    assert di.classify("misc/notes.md", "Thoughts", "some thoughts about lunch") == "general"


def test_insights_pull_out_commands_endpoints_settings_owners_and_warnings():
    r = di.insights("ops/runbooks/restart-api.md", "Restart the payments API", RUNBOOK)
    assert r["kind"] == "runbook" and r["kind_label"] == "Runbook" and r["purpose"].startswith("Use this runbook when")
    assert r["commands"][:2] == ["kubectl rollout restart deploy/payments", "kubectl get pods -l app=payments"]
    assert r["endpoints"] == ["GET /healthz"] and {"PAYMENTS_DB_URL", "RETRY_LIMIT"} <= set(r["settings"])
    assert {"role": "owner", "who": "platform-team"} in r["owners"] and any(o["role"] == "on-call" for o in r["owners"])
    assert any(w["level"] == "warning" and "settlement window" in w["text"] for w in r["warnings"])
    assert r["steps"] >= 2 and r["last_dated"] == "2026-09-30" and r["reading_minutes"] == 1


def test_long_sections_split_at_paragraphs_and_never_inside_code_or_tables():
    para = lambda i: "\n".join(f"Sentence {i}.{j} about the retry policy." for j in range(6))  # noqa: E731
    code = "```bash\n" + "\n".join(f"echo step{k}" for k in range(20)) + "\n```"
    table = "\n".join(["| a | b |", "|---|---|", *[f"| {k} | v |" for k in range(15)]])
    text = (
        "# Big\n\n"
        + "\n\n".join([para(0), para(1), para(2), code, para(3), table, para(4), para(5), para(6), para(7), para(8)])
        + "\n"
    )
    chunks = parse_markdown("big.md", text)
    assert len(chunks) > 1 and all(c.content.count("```") % 2 == 0 for c in chunks)  # fences stay balanced
    assert all(len(c.content.splitlines()) <= WINDOW + MAX_SECTION_LINES // 2 for c in chunks)
    tabled = [c for c in chunks if "| a | b |" in c.content]
    assert tabled and all(c.content.count("| v |") == 15 for c in tabled)  # the whole table is in one chunk
    covered = "\n".join(c.content for c in chunks)
    assert all(f"Sentence {i}.0" in covered for i in range(9))  # nothing is lost
    assert chunks[0].start_line == 1 and chunks[-1].end_line >= len(text.splitlines()) - 1
