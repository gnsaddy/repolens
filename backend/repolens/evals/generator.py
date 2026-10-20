"""Synthetic regression benchmark.

Each case is a small Python project with realistic structure (service, repository, API, config, utils, tests, docs),
a commit history with noise, and ONE deliberately introduced regression whose commit, file, symbol and changed lines
are recorded as ground truth. Generation is deterministic for a given seed (fixed dates and authors => stable SHAs).
"""

import json
import os
import random
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from string import Template

from ..services import gitops

DOMAINS = [
    {"pkg": "inventory", "noun": "item", "plural": "items", "Noun": "Item", "unit": "stock level"},
    {"pkg": "billing", "noun": "invoice", "plural": "invoices", "Noun": "Invoice", "unit": "balance"},
    {"pkg": "notify", "noun": "message", "plural": "messages", "Noun": "Message", "unit": "delivery count"},
    {"pkg": "bookings", "noun": "reservation", "plural": "reservations", "Noun": "Reservation", "unit": "seat count"},
    {"pkg": "catalog", "noun": "product", "plural": "products", "Noun": "Product", "unit": "inventory"},
    {"pkg": "shipping", "noun": "parcel", "plural": "parcels", "Noun": "Parcel", "unit": "weight"},
]

SERVICE = Template('''"""$Noun service: business rules for $plural."""
import time
from decimal import ROUND_HALF_UP, Decimal

from .config import MAX_ATTEMPTS, REQUEST_TIMEOUT
from .repository import ${Noun}Repository


class ${Noun}Service:
    def __init__(self, repo: ${Noun}Repository, cache=None):
        self.repo = repo
        self.cache = cache if cache is not None else {}

    def get_${noun}(self, ${noun}_id):
        record = self.repo.find_by_id(${noun}_id)
        if record is None:
            return {"id": ${noun}_id, "name": "unknown", "status": "missing"}
        return {"id": ${noun}_id, "name": record["name"], "status": record["status"]}

    def list_${plural}(self, page, size):
        records = self.repo.all()
        start = (page - 1) * size
        return records[start : start + size]

    def fetch_with_retry(self, ${noun}_id):
        last_error = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                return self.repo.load_remote(${noun}_id, timeout=REQUEST_TIMEOUT)
            except TimeoutError as exc:
                last_error = exc
                time.sleep(0.1 * (attempt + 1))
        raise last_error

    def reserve(self, ${noun}_id, qty):
        if qty <= 0:
            raise ValueError("quantity must be positive")
        record = self.repo.find_by_id(${noun}_id)
        record["reserved"] = record.get("reserved", 0) + qty
        return record

    def find_by_name(self, name):
        key = name.strip().lower()
        return self.repo.by_name(key)

    def total_price(self, lines):
        total = sum(Decimal(str(line["price"])) * line["qty"] for line in lines)
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def cache_key(self, tenant, ${noun}_id):
        return f"{tenant}:{${noun}_id}"

    def transfer(self, source, dest, qty):
        self.repo.move(source, dest, qty)
        return {"from": source, "to": dest, "qty": qty}
''')

REPOSITORY = Template('''"""Persistence for $plural."""


class ${Noun}Repository:
    def __init__(self, db):
        self.db = db

    def find_by_id(self, record_id):
        return self.db.get(record_id)

    def all(self):
        return list(self.db.values())

    def by_name(self, name):
        return next((r for r in self.db.values() if r["name"].lower() == name), None)

    def load_remote(self, record_id, timeout):
        return self.db.get(record_id)

    def move(self, source, dest, qty):
        self.db.setdefault("moves", []).append((source, dest, qty))
''')

CONFIG = Template('''"""Runtime settings for the $pkg package."""
REQUEST_TIMEOUT = 30
MAX_ATTEMPTS = 3
PAGE_SIZE = 20
''')

API = Template('''"""HTTP handlers for $plural."""
from .service import ${Noun}Service


def get_${noun}_endpoint(service: ${Noun}Service, ${noun}_id):
    record = service.repo.find_by_id(${noun}_id)
    if record is None:
        return {"status": 404, "error": "$noun not found"}
    return {"status": 200, "body": record}


def fetch_${noun}_endpoint(service: ${Noun}Service, ${noun}_id):
    return {"status": 200, "body": service.get_${noun}(${noun}_id)}


def list_${plural}_endpoint(service: ${Noun}Service, page=1, size=20):
    return {"status": 200, "body": service.list_${plural}(page, size)}


def reserve_${noun}_endpoint(service: ${Noun}Service, ${noun}_id, qty):
    return {"status": 200, "body": service.reserve(${noun}_id, qty)}
''')

UTILS = Template('''"""Small helpers."""


def format_${noun}_label(record):
    return f"{record['name']} ({record['status']})"


def slugify(text):
    return text.strip().lower().replace(" ", "-")
''')

TESTS = Template("""from $pkg.service import ${Noun}Service


class FakeRepo:
    def __init__(self):
        self.db = {1: {"name": "alpha", "status": "ok"}}

    def find_by_id(self, record_id):
        return self.db.get(record_id)

    def all(self):
        return list(self.db.values())

    def by_name(self, name):
        return self.db.get(1) if name == "alpha" else None

    def load_remote(self, record_id, timeout):
        return self.db.get(record_id)

    def move(self, source, dest, qty):
        self.moved = (source, dest, qty)


def test_get_${noun}_returns_record():
    assert ${Noun}Service(FakeRepo()).get_${noun}(1)["name"] == "alpha"


def test_list_${plural}_first_page():
    assert len(${Noun}Service(FakeRepo()).list_${plural}(1, 10)) == 1


def test_find_by_name_is_case_insensitive():
    assert ${Noun}Service(FakeRepo()).find_by_name("  ALPHA ") is not None
""")

README = Template("""# $pkg

Service for managing $plural. See the [design notes](docs/design.md).

Run the tests with `pytest`.
""")

DESIGN = Template("""# $Noun design

## Lookup
`${Noun}Service.get_${noun}` loads a $noun from `${Noun}Repository.find_by_id` and builds the response.
The HTTP entry point is `get_${noun}_endpoint` in `$pkg/api.py`.

## Listing
`list_${plural}` returns one page of $plural; pages are numbered from 1.

## Reliability
`fetch_with_retry` retries remote loads using `MAX_ATTEMPTS` and `REQUEST_TIMEOUT` from `$pkg/config.py`.

## Pricing
`total_price` sums line items and rounds to cents with `ROUND_HALF_UP`.
""")

NOISE_DOCSTRINGS = {
    "get_${noun}": "Return a lightweight view of one $noun.",
    "list_${plural}": "Return one page of $plural.",
    "reserve": "Reserve $unit for a $noun.",
    "total_price": "Sum the lines of an order.",
    "transfer": "Move $unit between locations.",
}


@dataclass
class BugTemplate:
    id: str
    file: str  # service | api | config
    symbol: str  # Template over domain, qualified; "" for module constants
    good: str
    bad: str
    anchor: str  # a line of `bad` that locates the failing line for tracebacks
    exception: str | None  # python exception line for traceback variants
    behavioral: str  # Template over domain
    feature: str  # Template over domain
    messages: list[str] = field(default_factory=list)


TEMPLATES: list[BugTemplate] = [
    BugTemplate(
        "null_guard",
        "service",
        "${Noun}Service.get_${noun}",
        '        if record is None:\n            return {"id": ${noun}_id, "name": "unknown", "status": "missing"}\n',
        "",
        'return {"id": ${noun}_id, "name": record["name"]',
        "TypeError: 'NoneType' object is not subscriptable",
        "Looking up a $noun that was deleted now fails with an error page. It used to show a placeholder entry.",
        "Opening the $noun detail page for a removed $noun returns HTTP 500 after yesterday's deploy.",
    ),
    BugTemplate(
        "off_by_one",
        "service",
        "${Noun}Service.list_${plural}",
        "        start = (page - 1) * size\n",
        "        start = page * size\n",
        "start = page * size",
        None,
        "The first page of the $plural list is missing entries and the last page comes back empty.",
        "GET /$plural?page=1 skips the first records and the final page comes back empty.",
    ),
    BugTemplate(
        "retry_removed",
        "service",
        "${Noun}Service.fetch_with_retry",
        "        for attempt in range(MAX_ATTEMPTS):\n",
        "        for attempt in range(1):\n",
        "raise last_error",
        "TimeoutError: remote store did not answer in time",
        "Loading a $noun from the remote store fails intermittently. Transient slowness used to heal itself, now every blip is an error.",
        "Intermittent timeouts when loading a $noun after the latest release; the remote store looks healthy.",
    ),
    BugTemplate(
        "missing_validation",
        "service",
        "${Noun}Service.reserve",
        '        if qty <= 0:\n            raise ValueError("quantity must be positive")\n',
        "",
        'record["reserved"] = record.get("reserved", 0) + qty',
        None,
        "Reserving a negative quantity is accepted and the $unit goes up instead of being rejected.",
        "POST reserve with qty=-5 returns 200 and the reserved count decreases; it used to answer with a validation error.",
    ),
    BugTemplate(
        "case_sensitivity",
        "service",
        "${Noun}Service.find_by_name",
        "        key = name.strip().lower()\n",
        "        key = name.strip()\n",
        "key = name.strip()",
        None,
        "Searching a $noun by name only works when typing it exactly as stored; capital letters find nothing.",
        "Name search for $plural became case sensitive; 'ALPHA' no longer finds 'alpha'.",
    ),
    BugTemplate(
        "rounding",
        "service",
        "${Noun}Service.total_price",
        '        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)\n',
        "        return Decimal(int(total))\n",
        "return Decimal(int(total))",
        None,
        "Order totals lose their cents: a total of 10.50 is reported as 10.",
        "Customers are charged whole numbers; fractional parts of the $plural total vanish in the receipt.",
    ),
    BugTemplate(
        "cache_key",
        "service",
        "${Noun}Service.cache_key",
        '        return f"{tenant}:{${noun}_id}"\n',
        '        return f"{${noun}_id}"\n',
        'return f"{${noun}_id}"',
        None,
        "One customer can see another customer's $plural after caching was switched on: data leaks between tenants.",
        "Tenant A is shown tenant B's $noun record; it only happens on cache hits.",
    ),
    BugTemplate(
        "wrong_default",
        "config",
        "",
        "REQUEST_TIMEOUT = 30\n",
        "REQUEST_TIMEOUT = 0\n",
        "REQUEST_TIMEOUT = 0",
        "TimeoutError: remote store did not answer in time",
        "Every call to the remote store now fails instantly, as if there were no time allowed for a response.",
        "All remote $noun loads time out immediately since the settings change; retries do not help.",
    ),
    BugTemplate(
        "swapped_args",
        "service",
        "${Noun}Service.transfer",
        "        self.repo.move(source, dest, qty)\n",
        "        self.repo.move(dest, source, qty)\n",
        "self.repo.move(dest, source, qty)",
        None,
        "Moving $unit between two locations goes the wrong way: the source gains what it should lose.",
        "Transfers between locations are reversed in the repository log; balances end up swapped.",
    ),
    BugTemplate(
        "status_code",
        "api",
        "get_${noun}_endpoint",
        '        return {"status": 404, "error": "$noun not found"}\n',
        '        return {"status": 200, "error": "$noun not found"}\n',
        'return {"status": 200, "error": "$noun not found"}',
        None,
        "API clients treat a missing $noun as a success because the response code no longer signals the problem.",
        "GET /$plural/999 answers 200 with an error message instead of a 404.",
    ),
]

MISLEADING_MESSAGES = [
    "refactor: simplify {noun} handling",
    "chore: tidy {noun} service",
    "perf: streamline {plural} processing",
    "refactor: clean up defaults for {noun} requests",
    "style: reduce branching in {pkg}",
]


@dataclass
class Case:
    id: str
    domain: str
    template: str
    variant: str
    bug_report: str
    gold_commit: str
    gold_file: str
    gold_symbol: str
    gold_lines: list[int]
    recurrence_of: str | None
    repo_dir: str
    commits: int


def _run(path: Path, *args: str, date: str | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Eval Dev",
        "GIT_AUTHOR_EMAIL": "dev@eval.local",
        "GIT_COMMITTER_NAME": "Eval Dev",
        "GIT_COMMITTER_EMAIL": "dev@eval.local",
    }
    if date:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _commit(path: Path, files: dict[str, str], msg: str, date: str) -> str:
    for rel, content in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    _run(path, "add", "-A")
    _run(path, "commit", "-q", "-m", msg, date=date)
    return _run(path, "rev-parse", "HEAD")


def _sub(text: str, d: dict) -> str:
    return Template(text).safe_substitute(d)


def _sources(d: dict) -> dict[str, str]:
    p = d["pkg"]
    return {
        f"{p}/__init__.py": "",
        f"{p}/service.py": SERVICE.substitute(d),
        f"{p}/repository.py": REPOSITORY.substitute(d),
        f"{p}/config.py": CONFIG.substitute(d),
        f"{p}/api.py": API.substitute(d),
        f"{p}/utils.py": UTILS.substitute(d),
        "tests/test_service.py": TESTS.substitute(d),
        "README.md": README.substitute(d),
        "docs/design.md": DESIGN.substitute(d),
    }


def _traceback(t: BugTemplate, d: dict, src_after: str, rng: random.Random) -> str | None:
    if not t.exception:
        return None
    path = f"{d['pkg']}/{'config' if t.file == 'config' else t.file}.py"
    anchor = _sub(t.anchor, d)
    line = next((i for i, ln in enumerate(src_after.splitlines(), 1) if anchor in ln), 1)
    fn = _sub(t.symbol, d).split(".")[-1] if t.symbol else "fetch_with_retry"
    if t.id == "wrong_default":  # the timeout surfaces where the constant is used
        svc = _sources(d)[f"{d['pkg']}/service.py"]
        line = next(i for i, ln in enumerate(svc.splitlines(), 1) if "raise last_error" in ln)
        path, fn = f"{d['pkg']}/service.py", "fetch_with_retry"
    caller = f"{d['pkg']}/api.py"
    return (
        "Traceback (most recent call last):\n"
        f'  File "{caller}", line {rng.randint(8, 20)}, in fetch_{d["noun"]}_endpoint\n'
        f'    return {{"status": 200, "body": service.get_{d["noun"]}({d["noun"]}_id)}}\n'
        f'  File "{path}", line {line}, in {fn}\n'
        f"    {anchor.strip()}\n{t.exception}\n"
    )


def build_case(
    case_id: str, d: dict, t: BugTemplate, variant: str, rng: random.Random, out: Path, recurrence_of: str | None = None
) -> Case:
    repo = out / "repos" / case_id
    if repo.exists():
        subprocess.run(["rm", "-rf", str(repo)], check=True)
    repo.mkdir(parents=True)
    _run(repo, "init", "-q", "-b", "main")
    files = _sources(d)
    day = 0

    def date() -> str:
        nonlocal day
        day += rng.randint(2, 9)
        return f"2026-{3 + day // 30:02d}-{1 + day % 28:02d}T10:00:00Z"

    n = 0
    _commit(repo, files, f"feat: initial {d['noun']} service", date())
    n += 1
    noise = [
        (
            "docs: expand design notes",
            lambda: {"docs/design.md": files["docs/design.md"] + "\nNotes: keep handlers thin.\n"},
        ),
        (
            "feat: add slug helper tests",
            lambda: {
                "tests/test_utils.py": f"from {d['pkg']}.utils import slugify\n\n\ndef test_slug():\n    assert slugify(' A b ') == 'a-b'\n"
            },
        ),
        (
            "chore: bump page size",
            lambda: {
                f"{d['pkg']}/config.py": files[f"{d['pkg']}/config.py"].replace("PAGE_SIZE = 20", "PAGE_SIZE = 25")
            },
        ),
    ]
    rng.shuffle(noise)
    for msg, mk in noise[: rng.randint(1, 3)]:
        files.update(mk())
        _commit(repo, mk(), msg, date())
        n += 1
    # a noise commit inside the same file as the bug: adds a docstring to an unrelated function
    svc_key = f"{d['pkg']}/service.py"
    unrelated = [k for k in NOISE_DOCSTRINGS if _sub(k, d) != _sub(t.symbol, d).split(".")[-1]]
    key = rng.choice(unrelated)
    sig = f"    def {_sub(key, d)}("
    src = files[svc_key]
    start = src.index(sig)
    eol = src.index("\n", start) + 1
    files[svc_key] = src[:eol] + f'        """{_sub(NOISE_DOCSTRINGS[key], d)}"""\n' + src[eol:]
    _commit(repo, {svc_key: files[svc_key]}, f"docs: document {_sub(key, d)}", date())
    n += 1

    # --- the regression ---
    target = {"service": svc_key, "api": f"{d['pkg']}/api.py", "config": f"{d['pkg']}/config.py"}[t.file]
    good, bad = _sub(t.good, d), _sub(t.bad, d)
    assert good in files[target], f"{t.id}: good snippet not found"
    after = files[target].replace(good, bad, 1)
    msg = rng.choice(MISLEADING_MESSAGES).format(**d)
    bug_sha = _commit(repo, {target: after}, msg, date())
    files[target] = after
    n += 1
    _commit(
        repo,
        {"docs/design.md": files["docs/design.md"] + "\nSee also: operations runbook.\n"},
        "docs: link runbook",
        date(),
    )
    n += 1

    hunks = gitops.commit_hunks(str(repo), bug_sha).get(target, [(1, 1)])
    symbol = _sub(t.symbol, d)
    behavioral, feature = _sub(t.behavioral, d), _sub(t.feature, d)
    tb = _traceback(t, d, after, rng)
    if variant == "trace" and tb:
        report = f"{behavioral.split('.')[0]}.\n\n{tb}"
    else:
        variant = "feature" if variant == "feature" else "behavioral"
        report = feature if variant == "feature" else behavioral
    return Case(
        case_id,
        d["pkg"],
        t.id,
        variant,
        report.strip(),
        bug_sha,
        target,
        symbol,
        [hunks[0][0], hunks[-1][1]],
        recurrence_of,
        f"repos/{case_id}",
        n,
    )


def generate(out: Path, n_cases: int = 20, seed: int = 7) -> list[Case]:
    """First pass: one case per template (varied domains). Further passes repeat templates in other domains, which
    is what the 'with local memory' configuration can exploit."""
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    cases: list[Case] = []
    first: dict[str, str] = {}
    variants = ["trace", "behavioral", "feature"]
    for i in range(n_cases):
        t = TEMPLATES[i % len(TEMPLATES)]
        d = DOMAINS[(i * 5 + (i // len(TEMPLATES)) * 3 + rng.randint(0, 5)) % len(DOMAINS)]
        cid = f"case-{i + 1:02d}-{t.id}-{d['pkg']}"
        c = build_case(cid, d, t, variants[i % 3], rng, out, first.get(t.id))
        first.setdefault(t.id, cid)
        cases.append(c)
    (out / "manifest.json").write_text(json.dumps([asdict(c) for c in cases], indent=2))
    return cases


def load_manifest(out: Path) -> list[Case]:
    return [Case(**c) for c in json.loads((out / "manifest.json").read_text())]
