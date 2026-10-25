"""python -m repolens.evals.suites --suite docs,memory,routing [--embedder hash] [--out DIR]
[--baseline FILE [--update-baseline | --check]]

Embedders other than the local hash one are OpenAI-compatible endpoints, e.g. a local Ollama:
  --embedder http --embed-base-url http://localhost:11434/v1 --embed-model nomic-embed-text --embed-dim 768 --embed-no-dimensions
The API key (if any) is read from the environment variable named by --embed-key-env, never from an argument."""

import argparse
import json
import sys
from pathlib import Path

from . import docs, memory, routing
from .embedders import HASH, EmbedderSpec

# metric -> direction ("up" = higher is better) and the slack allowed before the gate fails
GATES = {
    "docs": {"top1": ("up", 0.05), "top3": ("up", 0.05), "mrr": ("up", 0.04), "refusal_auc": ("up", 0.08)},
    "memory": {"f1": ("up", 0.05), "precision": ("up", 0.03), "wrong_reuse_rate": ("down", 0.03)},
    "routing": {
        "savings_cost_vs_all_strong": ("down", 0.05),
        "normal_cost_vs_all_strong": ("down", 0.05),
        "savings_hard_under_routed": ("down", 0.0),  # a hard task must never land on a weaker model
        "normal_hard_under_routed": ("down", 0.0),
        "normal_under_routed": ("down", 0.03),
    },
}


def compare(current: dict, baseline: dict) -> list[str]:
    """Human-readable regressions of `current` against `baseline` (empty = fine)."""
    problems = []
    for suite, metrics in GATES.items():
        if suite not in current or suite not in baseline:
            continue
        for name, (direction, slack) in metrics.items():
            cur, base = current[suite].get(name), baseline[suite].get(name)
            if cur is None or base is None:
                continue
            worse = cur < base - slack if direction == "up" else cur > base + slack
            if worse:
                problems.append(f"{suite}.{name}: {cur} vs baseline {base} (allowed slack {slack})")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RepoLens document, memory and routing evaluation suites (offline)")
    ap.add_argument("--suite", default="docs,memory,routing")
    ap.add_argument("--embedder", default="hash", choices=["hash", "http"])
    ap.add_argument("--name", default="", help="label for the embedder in the report")
    ap.add_argument("--embed-base-url", default="")
    ap.add_argument("--embed-model", default="")
    ap.add_argument("--embed-key-env", default="")
    ap.add_argument("--embed-dim", type=int, default=256)
    ap.add_argument(
        "--embed-no-dimensions", action="store_true", help="do not send the `dimensions` field (Ollama, Voyage...)"
    )
    ap.add_argument("--rerank", default="lexical", choices=["none", "lexical"])
    ap.add_argument("--out", default="")
    ap.add_argument("--baseline", default="")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--check", action="store_true", help="exit 1 if a metric is worse than the baseline")
    a = ap.parse_args(argv)
    spec = (
        HASH
        if a.embedder == "hash"
        else EmbedderSpec(
            a.name or a.embed_model,
            "openai",
            a.embed_base_url,
            a.embed_model,
            a.embed_key_env,
            a.embed_dim,
            not a.embed_no_dimensions,
        )
    )
    wanted = {s.strip() for s in a.suite.split(",") if s.strip()}
    res: dict = {}
    if "docs" in wanted:
        res["docs"] = docs.run(spec, rerank=a.rerank)
    if "memory" in wanted:
        res["memory"] = memory.run(spec)
    if "routing" in wanted:
        res["routing"] = routing.run()
    print(json.dumps(res, indent=2))
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / f"suites-{spec.name}.json").write_text(json.dumps(res, indent=2) + "\n")
    if a.baseline and a.update_baseline:
        Path(a.baseline).parent.mkdir(parents=True, exist_ok=True)
        Path(a.baseline).write_text(json.dumps(res, indent=2) + "\n")
        print(f"baseline written: {a.baseline}")
    if a.baseline and a.check:
        problems = compare(res, json.loads(Path(a.baseline).read_text()))
        for p in problems:
            print("REGRESSION", p, file=sys.stderr)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
