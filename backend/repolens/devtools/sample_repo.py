"""Create a small git repository with a realistic regression, to try RepoLens without using your own code.

    python -m repolens.devtools.sample_repo ./sample-repo

Run it from the backend folder (it reuses the repository the test suite builds). Then add the folder as a local repository
and paste the printed bug report into a new investigation."""

import sys
from pathlib import Path


def main() -> None:
    from tests.demo_repo import BUG_REPORT, build_demo_repo

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "sample-repo").resolve()
    if target.exists() and any(target.iterdir()):
        sys.exit(f"{target} already has files; pick an empty folder")
    sha = build_demo_repo(target)
    print(
        f"Sample repository created at {target}\nThe commit that introduced the bug: {sha}\n\nBug report to paste:\n\n{BUG_REPORT}"
    )


if __name__ == "__main__":
    main()
