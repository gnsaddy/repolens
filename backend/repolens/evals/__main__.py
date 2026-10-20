"""python -m repolens.evals --out evals/results [--cases 20] [--seed 7]"""

import argparse
import tempfile
from pathlib import Path

from . import report
from .harness import run_benchmark


def main() -> None:
    ap = argparse.ArgumentParser(description="RepoLens retrieval / RCA / risk benchmark (offline, synthetic)")
    ap.add_argument("--out", default="evals/results", help="directory for report.md and results.json")
    ap.add_argument("--work", default="", help="scratch directory for generated repositories (default: temp dir)")
    ap.add_argument("--cases", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-org-wide", action="store_true", help="skip the repository-unknown scenario")
    ap.add_argument("--no-risk", action="store_true", help="skip change-risk evaluation")
    a = ap.parse_args()
    work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="repolens-eval-"))
    res = run_benchmark(work, a.cases, a.seed, org_wide=not a.no_org_wide, risk=not a.no_risk)
    path = report.write(res, Path(a.out))
    manifest = work / "dataset" / "manifest.json"
    if manifest.exists():
        (Path(a.out) / "manifest.json").write_text(manifest.read_text())
    print(f"report: {path}")


if __name__ == "__main__":
    main()
