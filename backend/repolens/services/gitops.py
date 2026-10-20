"""Thin, read-only wrapper around the git CLI. Immutable SHAs are the source identifiers."""

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class GitError(Exception):
    pass


def _auth_env(auth_header: str | None) -> dict[str, str]:
    """Pass an HTTP auth header through git config env vars (never argv, so it is not visible in `ps`)."""
    if not auth_header:
        return {}
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": auth_header}


_SCHEMES: tuple[str, ...] = ("https://", "ssh://", "git@")  # tests extend this with file://
_BRANCH_RE = re.compile(r"^[\w][\w./+@-]{0,199}$")


def valid_branch(name: str) -> bool:
    return bool(_BRANCH_RE.match(name)) and ".." not in name and not name.endswith((".lock", "/", "."))


def _git(
    path: str,
    *args: str,
    timeout: int = 120,
    auth_header: str | None = None,
    stdin: str | None = None,
    env_extra: dict[str, str] | None = None,
) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", path, "-c", "core.quotepath=off", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            input=stdin,
            env={
                "GIT_TERMINAL_PROMPT": "0",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": str(Path.home()),
                **_auth_env(auth_header),
                **(env_extra or {}),
            },
        )
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out") from e
    if r.returncode != 0:
        raise GitError(r.stderr.strip() or f"git {args[0]} failed")
    return r.stdout


def is_repo(path: str) -> bool:
    try:
        _git(path, "rev-parse", "--git-dir")
        return True
    except (GitError, FileNotFoundError):
        return False


def clone(
    url: str, dest: str, auth_header: str | None = None, branch: str | None = None, filter_spec: str = ""
) -> None:
    """Clone without a working tree (everything is read from git objects). With `filter_spec="blob:none"` file
    contents are not downloaded until needed, which makes large repositories several times smaller and faster."""
    if not url.startswith(_SCHEMES):
        raise GitError("only https/ssh clone URLs are allowed")
    if branch and not valid_branch(branch):
        raise GitError(f"invalid branch name: {branch}")
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    args = ["git", "clone", "--quiet", "--no-checkout"]
    if filter_spec:
        args.append(f"--filter={filter_spec}")
    if branch:
        args += ["--single-branch", "--branch", branch]
    r = subprocess.run(
        [*args, "--", url, dest],
        capture_output=True,
        text=True,
        timeout=900,
        env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin", **_auth_env(auth_header)},
    )
    if r.returncode != 0:
        raise GitError(r.stderr.strip())


def fetch(path: str, auth_header: str | None = None) -> None:
    _git(path, "fetch", "--quiet", "--all", timeout=600, auth_header=auth_header)


def fetch_branch(path: str, branch: str, auth_header: str | None = None) -> None:
    """Fetch one branch (adds it to a single-branch clone) without touching the others."""
    if not valid_branch(branch):
        raise GitError(f"invalid branch name: {branch}")
    spec = f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
    _git(path, "fetch", "--quiet", "--no-tags", "origin", spec, timeout=600, auth_header=auth_header)


def is_partial(path: str) -> bool:
    try:
        return _git(path, "config", "--get", "remote.origin.promisor").strip() == "true"
    except GitError:
        return False


def prefetch_blobs(path: str, ref: str, wanted, auth_header: str | None = None) -> int:
    """Download the missing file contents of `ref` in one request instead of one round trip per file.
    `wanted(path)` selects the files that will be indexed. Best effort: lazy fetching still works otherwise."""
    # no --long: asking for blob sizes would itself download every blob, one request each
    rows = _git(path, "ls-tree", "-r", "-z", ref, timeout=300).split("\0")
    oids = []
    for row in rows:
        meta, _, name = row.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob" and wanted(name):
            oids.append(parts[2])
    if not oids:
        return 0
    checked = _git(
        path, "cat-file", "--batch-check", stdin="\n".join(oids) + "\n", env_extra={"GIT_NO_LAZY_FETCH": "1"}
    )
    missing = [ln.split()[0] for ln in checked.splitlines() if ln.endswith(" missing")]
    for i in range(0, len(missing), 2000):
        _git(
            path,
            "-c",
            "fetch.negotiationAlgorithm=noop",  # without this, asking for thousands of objects stalls in negotiation
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-write-fetch-head",
            "--recurse-submodules=no",
            "--filter=blob:none",
            "origin",
            "--stdin",
            stdin="\n".join(missing[i : i + 2000]) + "\n",
            timeout=900,
            auth_header=auth_header,
        )
    return len(missing)


def list_remote_branches(url: str, auth_header: str | None = None) -> tuple[list[str], str]:
    """Branch names and the default branch of a remote, without cloning."""
    if not url.startswith(_SCHEMES):
        raise GitError("only https/ssh URLs are allowed")
    try:
        r = subprocess.run(
            ["git", "ls-remote", "--symref", "--", url, "HEAD", "refs/heads/*"],
            capture_output=True,
            text=True,
            timeout=45,
            env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin", **_auth_env(auth_header)},
        )
    except subprocess.TimeoutExpired as e:
        raise GitError("listing branches timed out") from e
    if r.returncode != 0:
        raise GitError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "could not reach the repository")
    branches, default = [], ""
    for line in r.stdout.splitlines():
        if line.startswith("ref: refs/heads/"):
            default = line.split("\t")[0][len("ref: refs/heads/") :]
        elif "\trefs/heads/" in line:
            branches.append(line.split("\trefs/heads/", 1)[1])
    return sorted(branches), default


def list_local_branches(path: str) -> tuple[list[str], str]:
    out = _git(path, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return sorted(b for b in out.split() if b), current_branch(path)


def head_sha(path: str, ref: str = "HEAD") -> str:
    return _git(path, "rev-parse", ref).strip()


def current_branch(path: str) -> str:
    try:
        return _git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except GitError:
        return "main"


def list_files(path: str, ref: str = "HEAD") -> list[str]:
    return [f for f in _git(path, "ls-tree", "-r", "--name-only", ref).splitlines() if f]


def read_file(path: str, ref: str, file: str) -> bytes:
    r = subprocess.run(["git", "-C", path, "show", f"{ref}:{file}"], capture_output=True, timeout=60)
    if r.returncode != 0:
        raise GitError(r.stderr.decode(errors="replace"))
    return r.stdout


@dataclass
class CommitInfo:
    sha: str
    parent: str
    author: str
    committed_at: datetime
    message: str
    files: list[dict]


_SEP = "\x1e"
_FIELD = "\x1f"


def log(path: str, limit: int = 300, since_sha: str | None = None, ref: str = "HEAD") -> list[CommitInfo]:
    rng = f"{since_sha}..{ref}" if since_sha else ref
    fmt = _SEP + _FIELD.join(["%H", "%P", "%an", "%cI", "%B"]) + _FIELD
    out = _git(path, "log", rng, f"-n{limit}", "--numstat", f"--format={fmt}")
    commits: list[CommitInfo] = []
    for block in out.split(_SEP):
        if not block.strip():
            continue
        parts = block.split(_FIELD)
        if len(parts) < 6:
            continue
        sha, parents, author, date, message, tail = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        files = []
        for line in tail.splitlines():
            cols = line.split("\t")
            if len(cols) == 3:
                a, d, p = cols
                files.append(
                    {
                        "path": p,
                        "additions": int(a) if a.isdigit() else 0,
                        "deletions": int(d) if d.isdigit() else 0,
                    }
                )
        commits.append(
            CommitInfo(
                sha.strip(),
                parents.split(" ")[0] if parents else "",
                author,
                datetime.fromisoformat(date).astimezone(UTC),
                message.strip(),
                files,
            )
        )
    return commits


def diff_excerpt(path: str, sha: str, max_chars: int = 6000) -> str:
    try:
        out = _git(path, "show", "--format=", "--unified=2", "--no-color", sha)
    except GitError:
        return ""
    return out[:max_chars]


def blame_line(path: str, ref: str, file: str, line: int) -> dict | None:
    try:
        out = _git(path, "blame", "-L", f"{line},{line}", "--porcelain", ref, "--", file)
    except GitError:
        return None
    first = out.splitlines()[0].split()
    return {"sha": first[0]} if first else None


_BLAME_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


def blame_range(path: str, ref: str, file: str, start: int, end: int) -> dict[str, int]:
    """{commit sha: number of lines in [start, end] whose current text that commit last wrote}."""
    if start < 1 or end < start or end - start > 2000:
        return {}
    try:
        out = _git(path, "blame", "-L", f"{start},{end}", "--porcelain", ref, "--", file)
    except GitError:
        return {}
    counts: dict[str, int] = {}
    for line in out.splitlines():
        m = _BLAME_HEADER.match(line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return counts


_REF_OK = re.compile(r"^[A-Za-z0-9][\w./~^@{}-]{0,119}$")


def resolve_ref(path: str, ref: str) -> str:
    """Resolve a user-supplied ref to a commit SHA. Rejects option-like or odd refs before git sees them."""
    if not _REF_OK.match(ref):
        raise GitError(f"invalid ref: {ref!r}")
    return _git(path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()


def merge_base(path: str, a: str, b: str) -> str:
    return _git(path, "merge-base", a, b).strip()


def diff_numstat(path: str, base_sha: str, head_sha: str) -> list[dict]:
    out = _git(path, "diff", "--numstat", "--no-color", "--no-ext-diff", base_sha, head_sha)
    files = []
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) == 3:
            a, d, p = cols
            files.append(
                {"path": p, "additions": int(a) if a.isdigit() else 0, "deletions": int(d) if d.isdigit() else 0}
            )
    return files


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_hunks(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """New-side line ranges per file from a `-U0` unified diff. Pure deletions map to the line they happened at."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    cur: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
            if cur:
                ranges.setdefault(cur, [])
        elif cur and (m := _HUNK.match(line)):
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            ranges[cur].append((start, start + max(count, 1) - 1))
    return ranges


def commit_hunks(path: str, sha: str) -> dict[str, list[tuple[int, int]]]:
    try:
        parent = _git(path, "rev-parse", "--verify", "--quiet", f"{sha}^").strip()
        text = _git(path, "diff", "-U0", "--no-color", "--no-ext-diff", parent, sha)
    except GitError:
        text = _git(path, "show", "-U0", "--format=", "--no-color", sha)
    return parse_hunks(text)


def range_hunks(path: str, base_sha: str, head_sha: str) -> dict[str, list[tuple[int, int]]]:
    return parse_hunks(_git(path, "diff", "-U0", "--no-color", "--no-ext-diff", base_sha, head_sha))


def remote_url(path: str) -> str:
    try:
        return _git(path, "remote", "get-url", "origin").strip()
    except GitError:
        return ""
