"""Names that tie repositories together without an import: an image a pipeline builds and a manifest deploys, a
service and the URL that calls it, a queue a Terraform file creates and the code that uses it, an environment variable
one repository sets and another reads, a repository that a module, a workflow or a script points at.

Each extractor reads one kind of file and returns `Ident`s: a `kind`, a normalised `value`, and a `role`: **defines**
(this file creates or owns the name) or **refers** (it uses it). Joining a definition in one repository with a
reference in another is done elsewhere; here there is only reading. Everything is line and pattern based, never a YAML
or XML library, because these files come from repositories we do not control (anchors and entities can be made to
expand without limit), and only names are kept: never the values of environment variables, and never credentials in a
URL.

What it cannot see: names built at deploy time (Helm templates, Terragrunt variables, values from a vault) are skipped,
not guessed."""

import re
from dataclasses import dataclass
from posixpath import basename

KINDS = ("repo_ref", "image", "host", "queue", "env")
# names that every project has, so sharing one says nothing
GENERIC_IMAGES = {
    "alpine",
    "ubuntu",
    "debian",
    "busybox",
    "scratch",
    "python",
    "node",
    "golang",
    "openjdk",
    "nginx",
    "redis",
    "postgres",
    "mysql",
    "mongo",
    "rabbitmq",
    "memcached",
    "httpd",
    "traefik",
    "caddy",
    "ruby",
    "php",
    "rust",
    "eclipse-temurin",
}
GENERIC_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "example.com",
    "example.org",
    "host.docker.internal",
    "db",
    "web",
    "app",
}
GENERIC_ENV = {
    "PATH",
    "HOME",
    "PWD",
    "USER",
    "LANG",
    "PORT",
    "HOST",
    "DEBUG",
    "ENV",
    "NODE_ENV",
    "TZ",
    "SHELL",
    "TERM",
    "HOSTNAME",
    "CI",
    "LOG_LEVEL",
    "PYTHONPATH",
    "TMPDIR",
    "TEMP",
    "VERSION",
    "NAME",
    "MODE",
    "ENVIRONMENT",
    "APP_ENV",
}
QUEUE_TYPES = (
    "aws_sqs_queue",
    "aws_sns_topic",
    "aws_s3_bucket",
    "aws_dynamodb_table",
    "aws_kinesis_stream",
    "google_pubsub_topic",
    "google_pubsub_subscription",
    "google_storage_bucket",
    "azurerm_servicebus_queue",
    "azurerm_servicebus_topic",
    "azurerm_storage_container",
    "kafka_topic",
    "confluent_kafka_topic",
    "rabbitmq_queue",
    "aws_msk_topic",
)


@dataclass(frozen=True)
class Ident:
    kind: str
    value: str
    role: str  # defines | refers
    line: int
    note: str = ""  # where it was read: "a Kubernetes manifest", "a CI build step"...


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# --- normalising ---------------------------------------------------------------------------------------------------

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "dev.azure.com", "codeberg.org", "sr.ht")


def norm_repo(raw: str) -> str:
    """`https://user:pw@github.com/Acme/Lib.git`, `git@github.com:acme/lib`, `git::https://github.com/acme/lib//mod?ref=v1`,
    `github.com/acme/lib/pkg` all give `github.com/acme/lib`. "" when it is not a repository address."""
    s = raw.strip().strip("\"'").split("?")[0].split("#")[0]
    s = re.sub(r"^(git\+|git::)", "", s)
    m = re.match(r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/]+@)?([A-Za-z0-9.-]+)[:/]([^/]+)/([^/]+)", s)
    if not m:
        return ""
    host, owner, name = m.group(1).lower(), m.group(2), re.sub(r"\.git$", "", m.group(3))
    if "." not in host or not owner or not name or "{" in s or "$" in s:
        return ""
    return f"{host}/{owner}/{name}".lower()


def norm_image(raw: str) -> str:
    """`ghcr.io/Acme/payments:1.2@sha256:...` -> `ghcr.io/acme/payments`. "" for a template, a stage or a base image."""
    s = raw.strip().strip("\"',")
    s = s.split("@")[0]
    if ":" in s.rsplit("/", 1)[-1]:  # the tag may be a template (`:${{ github.sha }}`): only the name has to be literal
        s = s.rsplit(":", 1)[0]
    if not s or any(c in s for c in "{}$ <>"):
        return ""
    s = re.sub(r"^(docker\.io|index\.docker\.io|registry-1\.docker\.io)/", "", s).lower()
    return "" if s in GENERIC_IMAGES or s.removeprefix("library/") in GENERIC_IMAGES or len(s) < 3 else s


def host_of(url: str) -> str:
    m = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/\s]+@)?(\[[^\]]+\]|[A-Za-z0-9_.-]+)", url.strip().strip("\"'"), re.I)
    host = m.group(1).lower() if m else ""
    return (
        ""
        if not host
        or host in GENERIC_HOSTS
        or host in _GIT_HOSTS
        or len(host) < 3
        or "{" in host
        or "$" in host
        or host[0] == "."
        else host
    )


def clean_name(raw: str) -> str:
    s = raw.strip().strip("\"',")
    return "" if not s or any(c in s for c in "{}$ <>*") else s


# --- what kind of file is it ---------------------------------------------------------------------------------------


def classify(path: str) -> str:
    low = path.lower()
    name = basename(low)
    if name == "dockerfile" or name.startswith(("dockerfile.", "containerfile")) or name.endswith(".dockerfile"):
        return "dockerfile"
    if (
        ".github/workflows/" in low
        or name in (".gitlab-ci.yml", "jenkinsfile", "cloudbuild.yaml", "cloudbuild.yml", "bitbucket-pipelines.yml")
        or name.startswith("azure-pipelines")
        or ".circleci/" in low
    ):
        return "ci"
    if name.startswith(("docker-compose", "compose.")) and name.endswith((".yml", ".yaml")):
        return "compose"
    if name == ".gitmodules":
        return "gitmodules"
    if name.endswith((".tf", ".hcl", ".tfvars")):
        return "terraform"
    if name.endswith((".sh", ".bash", ".zsh", ".ps1")) or name in ("makefile", "justfile", "procfile"):
        return "shell"
    if name.endswith((".yaml", ".yml", ".tpl", ".j2")):
        return "yaml"
    if name.startswith(".env") and name.endswith((".example", ".sample", ".template", ".dist")):
        return "envfile"
    if name.endswith((".properties", ".ini", ".cfg", ".conf", ".toml", ".json")):
        return "config"
    return ""


# --- shared line patterns ------------------------------------------------------------------------------------------

_URL = re.compile(r"""\b([a-z][a-z0-9+.-]*://[^\s"'`<>)\]}]+)""", re.I)
_IMAGE_KEY = re.compile(r"""^\s*-?\s*image:\s*["']?([^\s"'#]+)""")
_DOCKER_BUILD = re.compile(r"\bdocker(?:\s+buildx)?\s+(?:build|bake)\b[^\n]*?(?:-t|--tag)[ =]+([^\s\"'\\]+)", re.I)
_DOCKER_PUSH = re.compile(r"\bdocker\s+push\s+([^\s\"'\\]+)", re.I)
_DOCKER_PULL = re.compile(
    r"\bdocker\s+(?:run|pull|create)\b[^\n]*?\s([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+(?::[A-Za-z0-9_.-]+)?)\s*(?:$|\\|\n)",
    re.I,
)
_GIT_CLONE = re.compile(
    r"\bgit\s+(?:clone|submodule\s+add)\b[^\n]*?\s((?:https?://|git@|ssh://|git://)[^\s\"'\\]+)", re.I
)
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


def _urls(text: str, role_note: str) -> list[Ident]:
    out = []
    for m in _URL.finditer(text):
        if h := host_of(m.group(1)):
            out.append(Ident("host", h, "refers", _line_of(text, m.start()), role_note))
    return out


def _repo_refs(text: str, note: str) -> list[Ident]:
    """Any git URL in the text, whatever the file: a module source, a submodule, a clone, a pinned git dependency."""
    out = []
    for m in re.finditer(
        r"""((?:git\+|git::)?(?:https?://|ssh://|git://)[^\s"'`<>)]+|git@[A-Za-z0-9.-]+:[^\s"'`<>)]+)""", text
    ):
        if (v := norm_repo(m.group(1))) and v.split("/")[0] in _GIT_HOSTS:
            out.append(Ident("repo_ref", v, "refers", _line_of(text, m.start()), note))
    return out


# --- one extractor per kind of file --------------------------------------------------------------------------------


def _dockerfile(text: str) -> list[Ident]:
    out, stages = [], set()
    for no, ln in enumerate(text.splitlines(), 1):
        if m := re.match(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", ln, re.I):
            if m.group(1).lower() not in stages and (img := norm_image(m.group(1))):
                out.append(Ident("image", img, "refers", no, "a Dockerfile base image"))
            if m.group(2):
                stages.add(m.group(2).lower())
    return out + _repo_refs(text, "a Dockerfile")


def _ci(text: str) -> list[Ident]:
    out = []
    for no, ln in enumerate(text.splitlines(), 1):
        if m := re.search(r"\buses:\s*([\w.-]+/[\w.-]+)(?:/[^@\s]*)?@\S+", ln):
            if not m.group(1).startswith("./"):
                out.append(
                    Ident("repo_ref", f"github.com/{m.group(1).lower()}", "refers", no, "a CI step that uses it")
                )
        if m := re.search(r"\brepository:\s*([\w.-]+/[\w.-]+)\s*$", ln):
            out.append(
                Ident("repo_ref", f"github.com/{m.group(1).lower()}", "refers", no, "a CI step that checks it out")
            )
    for m in [*_DOCKER_BUILD.finditer(text), *_DOCKER_PUSH.finditer(text)]:
        if img := norm_image(m.group(1)):
            out.append(Ident("image", img, "defines", _line_of(text, m.start()), "a CI build or push step"))
    for m in re.finditer(
        r"^\s*(?:images|tags):\s*\|?\s*\n((?:\s+-?\s*\S+\n?)+)", text, re.M
    ):  # build-push-action, metadata-action
        for t in re.findall(r"^\s*-?\s*([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+(?::\S+)?)\s*$", m.group(1), re.M):
            if img := norm_image(t):
                out.append(Ident("image", img, "defines", _line_of(text, m.start()), "a CI image tag"))
    for m in re.finditer(r"^\s*(?:images|tags):\s*(?:\[([^\]]*)\]|(\S+))\s*$", text, re.M):
        for t in re.split(r"[,\s]+", (m.group(1) or m.group(2) or "").strip("[]")):
            if "/" in t and (img := norm_image(t)):
                out.append(Ident("image", img, "defines", _line_of(text, m.start()), "a CI image tag"))
    return out + _repo_refs(text, "a CI file") + _urls(text, "a CI file")


def _compose(text: str) -> list[Ident]:
    out: list[Ident] = []
    lines = text.splitlines()
    services_at = next((i for i, ln in enumerate(lines) if re.match(r"^services:\s*$", ln)), -1)
    if services_at < 0:
        return out
    name, indent, builds, image, image_line = "", 0, False, "", 0

    def close() -> None:
        if image and (img := norm_image(image)):
            out.append(Ident("image", img, "defines" if builds else "refers", image_line, "a Compose service"))

    for no, ln in enumerate(lines[services_at + 1 :], services_at + 2):
        if ln.strip() and not ln.startswith(" "):
            break  # the next top-level key
        if m := re.match(r"^(\s{2,4})([A-Za-z0-9_.-]+):\s*$", ln):
            if name and len(m.group(1)) <= indent or not name:
                close()
                name, indent, builds, image = m.group(2), len(m.group(1)), False, ""
                if h := clean_name(name):
                    out.append(Ident("host", h.lower(), "defines", no, "a Compose service name"))
                continue
        if re.match(r"^\s+build:", ln):
            builds = True
        if m := _IMAGE_KEY.match(ln):
            image, image_line = m.group(1), no
    close()
    envs = re.finditer(r"^\s+-?\s*([A-Z][A-Z0-9_]{2,})\s*[=:]\s*(\S*)", text, re.M)
    for m in envs:
        if _ENV_NAME.match(m.group(1)) and m.group(1) not in GENERIC_ENV:
            out.append(Ident("env", m.group(1), "defines", _line_of(text, m.start()), "a Compose environment block"))
    return out + _urls(text, "a Compose file")


def _yaml(text: str) -> list[Ident]:
    """Kubernetes manifests, Helm values, Argo applications and anything else in YAML."""
    out: list[Ident] = []
    for no, ln in enumerate(text.splitlines(), 1):
        if "{{" in ln:
            continue  # a template: its value is only known at deploy time
        if m := _IMAGE_KEY.match(ln):
            if img := norm_image(m.group(1)):
                out.append(Ident("image", img, "refers", no, "a manifest image"))
        if m := re.match(r"^\s*repoURL:\s*(\S+)", ln):
            if v := norm_repo(m.group(1)):
                out.append(Ident("repo_ref", v, "refers", no, "an Argo CD application"))
        if m := re.match(r"^\s*-?\s*host:\s*[\"']?([A-Za-z0-9_.-]{3,})[\"']?\s*$", ln):
            if h := host_of(f"https://{m.group(1)}"):
                out.append(Ident("host", h, "defines", no, "an Ingress host"))
    for m in re.finditer(r"^\s*repository:\s*[\"']?([^\s\"'#]+)", text, re.M):  # Helm values: image: {repository: ...}
        before = text[: m.start()].rstrip().splitlines()
        if before and re.match(r"^\s*image:\s*$", before[-1]) and (img := norm_image(m.group(1))):
            out.append(Ident("image", img, "refers", _line_of(text, m.start()), "Helm values"))
    for doc_start, doc in _documents(text):
        kind = re.search(r"^kind:\s*(\w+)", doc, re.M)
        meta = re.search(r"^metadata:\s*\n(?:[ \t]+[^\n]*\n)*?[ \t]+name:\s*[\"']?([A-Za-z0-9_.-]+)", doc, re.M)
        if kind and meta and kind.group(1) in ("Service", "VirtualService") and (h := clean_name(meta.group(1))):
            out.append(
                Ident("host", h.lower(), "defines", doc_start + _line_of(doc, meta.start()) - 1, "a Kubernetes Service")
            )
    for m in re.finditer(r"-\s*name:\s*([A-Z][A-Z0-9_]{2,})\s*\n\s*value(?:From)?:", text):
        if m.group(1) not in GENERIC_ENV:
            out.append(Ident("env", m.group(1), "defines", _line_of(text, m.start()), "a Kubernetes env block"))
    return out + _urls(text, "a manifest") + _repo_refs(text, "a manifest")


def _documents(text: str) -> list[tuple[int, str]]:
    docs: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 1
    for no, ln in enumerate(text.splitlines(), 1):
        if ln.startswith("---"):
            docs.append((start, "\n".join(buf)))
            start, buf = no + 1, []
        else:
            buf.append(ln)
    docs.append((start, "\n".join(buf)))
    return docs


def _terraform(text: str) -> list[Ident]:
    out: list[Ident] = []
    for m in re.finditer(r"""\bsource\s*=\s*"([^"]+)"|\burl\s*=\s*"([^"]+)\"""", text):
        raw = m.group(1) or m.group(2)
        if (v := norm_repo(raw)) and v.split("/")[0] in _GIT_HOSTS:
            out.append(Ident("repo_ref", v, "refers", _line_of(text, m.start()), "a Terraform module source"))
    resource = re.compile(r'^resource\s+"(' + "|".join(QUEUE_TYPES) + r')"\s+"([^"]+)"\s*\{', re.M)
    for m in resource.finditer(text):
        body = text[m.end() : text.find("\n}", m.end()) if text.find("\n}", m.end()) > 0 else len(text)]
        named = re.search(r'^\s*(?:name|bucket|topic|queue_name|topic_name|stream_name)\s*=\s*"([^"$]+)"', body, re.M)
        if v := clean_name(named.group(1) if named else ""):
            out.append(Ident("queue", v, "defines", _line_of(text, m.start()), f"a Terraform {m.group(1)}"))
    for m in re.finditer(r'"image"\s*:\s*"([^"$]+)"|^\s*image\s*=\s*"([^"$]+)"', text, re.M):
        if img := norm_image(m.group(1) or m.group(2)):
            out.append(Ident("image", img, "refers", _line_of(text, m.start()), "a Terraform container definition"))
    for m in re.finditer(r"variables\s*=\s*\{([^}]*)\}", text):
        for k in re.findall(r"^\s*([A-Z][A-Z0-9_]{2,})\s*=", m.group(1), re.M):
            if k not in GENERIC_ENV:
                out.append(Ident("env", k, "defines", _line_of(text, m.start()), "a Terraform environment block"))
    return out + _urls(text, "a Terraform file")


def _shell(text: str) -> list[Ident]:
    out: list[Ident] = []
    for m in [*_DOCKER_BUILD.finditer(text), *_DOCKER_PUSH.finditer(text)]:
        if img := norm_image(m.group(1)):
            out.append(Ident("image", img, "defines", _line_of(text, m.start()), "a shell build or push command"))
    for m in _DOCKER_PULL.finditer(text):
        if img := norm_image(m.group(1)):
            out.append(Ident("image", img, "refers", _line_of(text, m.start()), "a shell run or pull command"))
    for m in _GIT_CLONE.finditer(text):
        if v := norm_repo(m.group(1)):
            out.append(Ident("repo_ref", v, "refers", _line_of(text, m.start()), "a git clone in a script"))
    for m in re.finditer(r"^\s*export\s+([A-Z][A-Z0-9_]{2,})=", text, re.M):
        if m.group(1) not in GENERIC_ENV:
            out.append(Ident("env", m.group(1), "defines", _line_of(text, m.start()), "a script that exports it"))
    return out + _urls(text, "a script")


def _gitmodules(text: str) -> list[Ident]:
    out = []
    for m in re.finditer(r"^\s*url\s*=\s*(\S+)", text, re.M):
        if v := norm_repo(m.group(1)):
            out.append(Ident("repo_ref", v, "refers", _line_of(text, m.start()), "a git submodule"))
    return out


def _envfile(text: str) -> list[Ident]:
    """`.env.example` lists the variables the application expects to be set."""
    out = []
    for no, ln in enumerate(text.splitlines(), 1):
        if m := re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{2,})\s*=", ln):
            if m.group(1) not in GENERIC_ENV:
                out.append(Ident("env", m.group(1), "refers", no, "an example environment file"))
    return out + _urls(text, "an example environment file")


def _config(text: str) -> list[Ident]:
    return _urls(text, "a configuration file") + _repo_refs(text, "a configuration file")


EXTRACTORS = {
    "dockerfile": _dockerfile,
    "ci": _ci,
    "compose": _compose,
    "yaml": _yaml,
    "terraform": _terraform,
    "shell": _shell,
    "gitmodules": _gitmodules,
    "envfile": _envfile,
    "config": _config,
}


def extract(path: str, text: str) -> list[Ident]:
    """Names read from one infrastructure, pipeline, script or configuration file. Never raises."""
    fn = EXTRACTORS.get(classify(path))
    if fn is None:
        return []
    try:
        return list(dict.fromkeys(fn(text[:400_000])))
    except Exception:  # noqa: BLE001 - a surprising file must not stop the others being read
        return []


# --- application code ----------------------------------------------------------------------------------------------

_ENV_READ = re.compile(
    r"""(?:os\.environ(?:\.get)?\s*[\[(]|os\.getenv\s*\(|process\.env\.|process\.env\[|System\.getenv\s*\(|\bgetenv\s*\(|"""
    r"""ENV\.fetch\s*\(|ENV\[|Environment\.GetEnvironmentVariable\s*\(|std::env::var\s*\(|env::var\s*\(|os\.LookupEnv\s*\()"""
    r"""\s*["']?([A-Z][A-Z0-9_]{2,})["']?"""
)
_QUOTED = re.compile(r"""["'`]([^"'`\n]{4,120})["'`]""")


def code_refs(text: str, names: set[str], hosts: set[str], want_env: bool) -> list[Ident]:
    """What application code refers to: environment variables it reads, URLs it calls (when some repository defines that
    host), and string literals equal to a queue, topic or bucket name some repository defines."""
    out: list[Ident] = []
    if want_env:
        for m in _ENV_READ.finditer(text):
            if m.group(1) not in GENERIC_ENV:
                out.append(
                    Ident("env", m.group(1), "refers", _line_of(text, m.start()), "code that reads the variable")
                )
    if hosts and "://" in text:
        for m in _URL.finditer(text):
            if (h := host_of(m.group(1))) and h in hosts:
                out.append(Ident("host", h, "refers", _line_of(text, m.start()), "code that calls the service"))
    if names:
        for m in _QUOTED.finditer(text):
            if m.group(1) in names:
                out.append(Ident("queue", m.group(1), "refers", _line_of(text, m.start()), "code that uses the name"))
    return list(dict.fromkeys(out))
