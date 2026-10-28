"""Package manifests: what a repository offers to others and what it says it needs, for the common ecosystems.

Pure parsing, no database. Each parser returns a `Manifest`: the packages the file names as its own (`provides`) and
the packages it asks for (`requires`), with the line each is on so a link can point at it. A file that cannot be read
gives an empty manifest with `error` set, never an exception: manifests come from repositories we do not control. XML
is read with patterns, not an XML library, because a hostile file could otherwise expand entities until the server
runs out of memory."""

import json
import re
import tomllib
from dataclasses import dataclass, field
from posixpath import basename

ECOSYSTEMS = ("python", "npm", "go", "maven", "cargo", "gem", "composer", "nuget")


@dataclass(frozen=True)
class Req:
    eco: str
    name: str  # as written
    spec: str = ""  # version or source, as written
    scope: str = "runtime"  # runtime | dev | optional | indirect
    line: int = 0


@dataclass
class Manifest:
    eco: str = ""
    provides: list[tuple[str, int]] = field(default_factory=list)  # (name as written, line)
    requires: list[Req] = field(default_factory=list)
    error: str = ""


def norm(eco: str, name: str) -> str:
    """The form in which two spellings of one package are equal (`user_lib`, `User-Lib` and `user.lib` for Python)."""
    name = name.strip()
    if eco == "python":
        return re.sub(r"[-_.]+", "-", name).lower()
    if eco == "cargo":
        return name.replace("_", "-").lower()
    return name.lower()


def _line(text: str, needle: str) -> int:
    i = text.find(needle) if needle else -1
    return text.count("\n", 0, i) + 1 if i >= 0 else 0


# --- Python -----------------------------------------------------------------------------

_PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")


def _pep508(text: str, raw: str, scope: str) -> Req | None:
    m = _PEP508.match(raw.split(";")[0].split("#")[0])
    if not m:
        return None
    spec = m.group(2).strip()
    return Req("python", m.group(1), spec[1:].strip() if spec.startswith("@") else spec, scope, _line(text, m.group(1)))


def _pyproject(text: str) -> Manifest:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return Manifest("python", error=f"not valid TOML: {str(e)[:80]}")
    m = Manifest("python")
    proj, poetry = data.get("project") or {}, (data.get("tool") or {}).get("poetry") or {}
    for name in (proj.get("name"), poetry.get("name")):
        if isinstance(name, str) and name:
            m.provides.append((name, _line(text, name)))
    for raw in proj.get("dependencies") or []:
        if isinstance(raw, str) and (r := _pep508(text, raw, "runtime")):
            m.requires.append(r)
    for group, items in (proj.get("optional-dependencies") or {}).items():
        for raw in items if isinstance(items, list) else []:
            if isinstance(raw, str) and (
                r := _pep508(text, raw, "dev" if "dev" in group or "test" in group else "optional")
            ):
                m.requires.append(r)
    for items in (data.get("dependency-groups") or {}).values():
        for raw in items if isinstance(items, list) else []:
            if isinstance(raw, str) and (r := _pep508(text, raw, "dev")):
                m.requires.append(r)
    for scope, table in (("runtime", poetry.get("dependencies")), ("dev", poetry.get("dev-dependencies"))):
        for key, spec in (table or {}).items():
            if key.lower() != "python":
                m.requires.append(Req("python", key, spec if isinstance(spec, str) else "", scope, _line(text, key)))
    for grp in (poetry.get("group") or {}).values():
        for key, spec in ((grp or {}).get("dependencies") or {}).items():
            m.requires.append(Req("python", key, spec if isinstance(spec, str) else "", "dev", _line(text, key)))
    return m


def _requirements(text: str) -> Manifest:
    m = Manifest("python")
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#")[0].strip()
        if not line or line.startswith(("-r", "-c", "--", "-i")):
            continue
        if line.startswith("-e") or "://" in line.split(" @ ")[0]:
            egg = re.search(r"#egg=([A-Za-z0-9._-]+)|egg=([A-Za-z0-9._-]+)", raw)
            if egg:
                m.requires.append(Req("python", egg.group(1) or egg.group(2), "source", "runtime", no))
            continue
        if r := _pep508(line, line, "runtime"):
            m.requires.append(Req("python", r.name, r.spec, "runtime", no))
    return m


# --- JavaScript and TypeScript -----------------------------------------------------------------------------


def _package_json(text: str) -> Manifest:
    try:
        data = json.loads(text)
    except ValueError as e:
        return Manifest("npm", error=f"not valid JSON: {str(e)[:80]}")
    if not isinstance(data, dict):
        return Manifest("npm", error="not a JSON object")
    m = Manifest("npm")
    if isinstance(data.get("name"), str) and data["name"]:
        m.provides.append((data["name"], _line(text, '"name"')))
    for key, scope in (
        ("dependencies", "runtime"),
        ("devDependencies", "dev"),
        ("peerDependencies", "runtime"),
        ("optionalDependencies", "optional"),
    ):
        deps = data.get(key)
        for name, spec in deps.items() if isinstance(deps, dict) else []:
            m.requires.append(Req("npm", name, str(spec), scope, _line(text, f'"{name}"')))
    return m


# --- Go -----------------------------------------------------------------------------


def _go_mod(text: str) -> Manifest:
    m = Manifest("go")
    if mod := re.search(r"^\s*module\s+(\S+)", text, re.M):
        m.provides.append((mod.group(1).strip('"'), _line(text, mod.group(0).strip())))
    blocks = re.findall(r"^\s*require\s*\(([^)]*)\)", text, re.M | re.S)
    singles = re.findall(r"^\s*require\s+([^\s(][^\n]*)$", text, re.M)
    for chunk in [*blocks, *singles]:
        for ln in chunk.splitlines():
            parts = ln.split("//")[0].split()
            if len(parts) >= 2:
                scope = "indirect" if "// indirect" in ln else "runtime"
                m.requires.append(Req("go", parts[0].strip('"'), parts[1], scope, _line(text, parts[0])))
    for src, dst in re.findall(r"^\s*replace\s+(\S+)(?:\s+\S+)?\s*=>\s*(\S+)", text, re.M):
        m.requires.append(Req("go", dst, f"replaces {src}", "runtime", _line(text, dst)))
    return m


# --- Java and Kotlin -----------------------------------------------------------------------------

_BLOCKS = re.compile(r"<(dependencies|dependencyManagement|build|profiles|reporting|parent|plugins)\b.*?</\1>", re.S)


def _tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*([^<]*?)\s*</{tag}>", block)
    return m.group(1) if m else ""


def _pom(text: str) -> Manifest:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    m = Manifest("maven")
    if "<project" not in text:
        return Manifest("maven", error="not a Maven project file")
    parent = re.search(r"<parent>.*?</parent>", text, re.S)
    own = _BLOCKS.sub("", text)
    artifact = _tag(own, "artifactId")
    group = _tag(own, "groupId") or (_tag(parent.group(0), "groupId") if parent else "")
    if artifact and group:
        m.provides.append((f"{group}:{artifact}", _line(text, f"<artifactId>{artifact}</artifactId>")))
    deps = re.search(
        r"<dependencies>.*?</dependencies>",
        re.sub(r"<dependencyManagement>.*?</dependencyManagement>", "", text, flags=re.S),
        re.S,
    )
    for d in re.findall(r"<dependency>.*?</dependency>", deps.group(0) if deps else "", re.S):
        g, a = _tag(d, "groupId"), _tag(d, "artifactId")
        if g and a:
            scope = "dev" if _tag(d, "scope") == "test" else "runtime"
            m.requires.append(
                Req("maven", f"{g}:{a}", _tag(d, "version"), scope, _line(text, f"<artifactId>{a}</artifactId>"))
            )
    return m


_GRADLE = re.compile(
    r"\b(implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|compile|testCompile|kapt|annotationProcessor)"
    r"\s*\(?\s*['\"]([^'\":\s]+):([^'\":\s]+)(?::([^'\"\s]+))?['\"]"
)


def _gradle(text: str) -> Manifest:
    m = Manifest("maven")
    for conf, g, a, ver in _GRADLE.findall(text):
        m.requires.append(
            Req("maven", f"{g}:{a}", ver, "dev" if conf.startswith("test") else "runtime", _line(text, f"{g}:{a}"))
        )
    group = re.search(r"^\s*group\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
    name = re.search(r"(?:archivesBaseName|rootProject\.name)\s*=\s*['\"]([^'\"]+)['\"]", text)
    if group and name:
        m.provides.append((f"{group.group(1)}:{name.group(1)}", _line(text, name.group(0))))
    return m


# --- Rust -----------------------------------------------------------------------------


def _cargo(text: str) -> Manifest:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return Manifest("cargo", error=f"not valid TOML: {str(e)[:80]}")
    m = Manifest("cargo")
    pkg = data.get("package") or {}
    if isinstance(pkg.get("name"), str):
        m.provides.append((pkg["name"], _line(text, pkg["name"])))
    for key, scope in (("dependencies", "runtime"), ("dev-dependencies", "dev"), ("build-dependencies", "dev")):
        for name, spec in (data.get(key) or {}).items():
            real = spec.get("package", name) if isinstance(spec, dict) else name
            version = spec if isinstance(spec, str) else (spec.get("version", "") if isinstance(spec, dict) else "")
            m.requires.append(Req("cargo", real, str(version), scope, _line(text, name)))
    return m


# --- Ruby, PHP, .NET -----------------------------------------------------------------------------


def _gemfile(text: str) -> Manifest:
    m = Manifest("gem")
    for _q, name, rest in re.findall(r"^\s*gem\s+(['\"])([^'\"]+)\1([^\n]*)", text, re.M):
        m.requires.append(Req("gem", name, rest.strip(" ,")[:60], "runtime", _line(text, name)))
    return m


def _gemspec(text: str) -> Manifest:
    m = Manifest("gem")
    if n := re.search(r"\.name\s*=\s*['\"]([^'\"]+)['\"]", text):
        m.provides.append((n.group(1), _line(text, n.group(0))))
    for kind, name in re.findall(r"\.(add_\w*dependency)\s*\(?\s*['\"]([^'\"]+)['\"]", text):
        m.requires.append(Req("gem", name, "", "dev" if "development" in kind else "runtime", _line(text, name)))
    return m


def _composer(text: str) -> Manifest:
    try:
        data = json.loads(text)
    except ValueError as e:
        return Manifest("composer", error=f"not valid JSON: {str(e)[:80]}")
    if not isinstance(data, dict):
        return Manifest("composer", error="not a JSON object")
    m = Manifest("composer")
    if isinstance(data.get("name"), str):
        m.provides.append((data["name"], _line(text, '"name"')))
    for key, scope in (("require", "runtime"), ("require-dev", "dev")):
        deps = data.get(key)
        for name, spec in deps.items() if isinstance(deps, dict) else []:
            if "/" in name:  # `php` and `ext-json` are the platform, not packages
                m.requires.append(Req("composer", name, str(spec), scope, _line(text, f'"{name}"')))
    return m


def _csproj(text: str, path: str) -> Manifest:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    m = Manifest("nuget")
    ident = re.search(r"<(?:PackageId|AssemblyName)>\s*([^<]+?)\s*</(?:PackageId|AssemblyName)>", text)
    m.provides.append(
        (ident.group(1) if ident else basename(path).rsplit(".", 1)[0], _line(text, ident.group(0)) if ident else 1)
    )
    for name, rest in re.findall(r"<PackageReference\s+Include=\"([^\"]+)\"([^>]*)>", text):
        ver = re.search(r"Version=\"([^\"]+)\"", rest)
        m.requires.append(Req("nuget", name, ver.group(1) if ver else "", "runtime", _line(text, f'Include="{name}"')))
    return m


# --- dispatch -----------------------------------------------------------------------------


def is_manifest(path: str) -> bool:
    low = basename(path).lower()
    return (
        low in ("pyproject.toml", "package.json", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "cargo.toml")
        or low in ("gemfile", "composer.json")
        or (low.startswith("requirements") and low.endswith((".txt", ".in")))
        or low.endswith((".gemspec", ".csproj"))
    )


def parse(path: str, text: str) -> Manifest:
    """Read one manifest. Never raises."""
    low = basename(path).lower()
    try:
        if low == "pyproject.toml":
            return _pyproject(text)
        if low.startswith("requirements"):
            return _requirements(text)
        if low == "package.json":
            return _package_json(text)
        if low == "go.mod":
            return _go_mod(text)
        if low == "pom.xml":
            return _pom(text)
        if low.startswith("build.gradle"):
            return _gradle(text)
        if low == "cargo.toml":
            return _cargo(text)
        if low == "gemfile":
            return _gemfile(text)
        if low.endswith(".gemspec"):
            return _gemspec(text)
        if low == "composer.json":
            return _composer(text)
        if low.endswith(".csproj"):
            return _csproj(text, path)
    except Exception as e:  # noqa: BLE001 - a surprising file must not stop the other manifests being read
        return Manifest(error=f"could not be read: {type(e).__name__}")
    return Manifest()
