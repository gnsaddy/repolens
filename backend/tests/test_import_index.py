import posixpath
import random

from repolens.services.graph import PathIndex, resolve_import


def naive_suffix(paths: set[str], c: str) -> set[str]:
    return {p for p in paths if p.endswith("/" + c)}  # the original scan the index replaces


def naive_go(paths: set[str], tail: str) -> set[str]:
    return {p for p in paths if p.endswith(".go") and posixpath.basename(posixpath.dirname(p)) == tail}


def make_paths(n: int, seed: int = 3) -> set[str]:
    rnd = random.Random(seed)
    names = ["util", "api", "core", "kubernetes", "client", "helpers", "x"]
    out = set()
    for _ in range(n):
        depth = rnd.randint(1, 5)
        d = "/".join(
            rnd.choice(names) + str(rnd.randint(0, 3)) if rnd.random() < 0.5 else rnd.choice(names)
            for _ in range(depth)
        )
        out.add(f"{d}/{rnd.choice(['a', 'b', 'main', 'util'])}{rnd.choice(['.go', '.py', '.java', '.ts'])}")
    return out


def test_the_index_answers_exactly_what_the_scan_did():
    paths = make_paths(400)
    idx = PathIndex(paths)
    queries = {
        "a.go",
        "util/main.py",
        "api0/util.go",
        "core/helpers/x.java",
        "kubernetes/b.go",
        "nothing/here.py",
        "main.go",
    }
    queries |= {"/".join(p.split("/")[i:]) for p in list(paths)[:60] for i in range(len(p.split("/")))}
    for q in queries:
        assert idx.with_suffix(q) == naive_suffix(paths, q), q
    for tail in ("util", "kubernetes", "client", "api1", "missing", "fmt"):
        assert idx.go_files_in_dirs_named(tail) == naive_go(paths, tail), tail


def test_resolving_gives_the_same_files_with_a_set_or_a_prebuilt_index():
    paths = {
        "src/pkg/util/helpers.go",
        "src/kubernetes/client.go",
        "src/app/main.go",
        "pkg/util/helpers.py",
        "src/user_profile.py",
        "src/repository.py",
        "com/acme/Foo.java",
    }
    idx = PathIndex(paths)
    cases = [
        ('"k8s.io/api/kubernetes"', "go", "src/app/main.go"),
        ('"fmt"', "go", "src/app/main.go"),
        ("import util.helpers", "python", "x.py"),
        ("from .repository import UserRepository", "python", "src/user_profile.py"),
        ("import com.acme.Foo;", "java", "x/Bar.java"),
    ]
    for stmt, lang, file in cases:
        assert resolve_import(stmt, lang, file, paths) == resolve_import(stmt, lang, file, idx), stmt
    assert resolve_import('"k8s.io/api/kubernetes"', "go", "src/app/main.go", idx) == {"src/kubernetes/client.go"}
    assert resolve_import("import util.helpers", "python", "x.py", idx) == {"pkg/util/helpers.py"}


def test_resolving_one_import_does_not_get_slower_as_the_repository_grows():
    import time

    small, big = PathIndex(make_paths(300, 1)), PathIndex(make_paths(30000, 2))
    for ix in (small, big):  # build the tables first: they are a one-off cost per repository
        ix.with_suffix("x")
        ix.go_files_in_dirs_named("x")

    def per_call(ix: PathIndex) -> float:
        t = time.perf_counter()
        for _ in range(2000):
            resolve_import('"k8s.io/api/kubernetes"', "go", "a.go", ix)
            resolve_import("import util.helpers", "python", "x.py", ix)
        return (time.perf_counter() - t) / 4000

    assert (
        per_call(big) < per_call(small) * 20 + 0.0005
    )  # 100x the files: a lookup, not a scan (a scan would be about 100x slower)
