"""Names that tie repositories without an import: what is read from each kind of file, and the links they give."""

import subprocess
import time

from repolens.models import RepoLink, Repository
from repolens.services import identifiers as ids
from repolens.services import repo_links as rl
from tests.test_api import add_repo
from tests.test_chat import make_user
from tests.test_manifests import make


def got(path, text, kind=None, role=None):
    return {
        (i.kind, i.value, i.role)
        for i in ids.extract(path, text)
        if (not kind or i.kind == kind) and (not role or i.role == role)
    }


# --- normalising ---------------------------------------------------------------------------------------------------


def test_every_spelling_of_a_repository_address_gives_one_name():
    same = [
        "https://user:pw@github.com/Acme/Lib.git",
        "git@github.com:acme/lib",
        "git::https://github.com/acme/lib//modules/x?ref=v1.2",
        "github.com/acme/lib/pkg/sub",
        "ssh://git@github.com/acme/lib.git",
        "git+https://github.com/acme/lib.git#egg=lib",
    ]
    assert {ids.norm_repo(s) for s in same} == {"github.com/acme/lib"}
    assert ids.norm_repo("https://example.com/") == "" and ids.norm_repo("not a url") == ""


def test_an_image_is_named_without_its_tag_digest_or_registry_alias_and_base_images_are_not_names():
    for s in (
        "ghcr.io/Acme/payments:1.2",
        "ghcr.io/acme/payments@sha256:abc",
        "ghcr.io/acme/payments:${{ github.sha }}",
    ):
        assert ids.norm_image(s) == "ghcr.io/acme/payments"
    assert ids.norm_image("docker.io/acme/payments:3") == "acme/payments"
    assert (
        ids.norm_image("python:3.12-slim") == ""
        and ids.norm_image("nginx") == ""
        and ids.norm_image("{{ .Values.image }}") == ""
    )
    assert ids.norm_image("${REGISTRY}/payments") == ""


def test_a_host_comes_from_a_url_without_credentials_ports_or_the_names_every_machine_has():
    assert ids.host_of("postgres://user:s3cr3t@db-main.internal:5432/app") == "db-main.internal"
    assert ids.host_of("http://users:8080/x") == "users" and ids.host_of("http://localhost:8000") == ""
    assert ids.host_of("https://${HOST}/x") == "" and ids.host_of("http://127.0.0.1") == ""


# --- each kind of file ---------------------------------------------------------------------------------------------


def test_a_dockerfile_refers_to_its_base_image_but_not_to_its_own_build_stages_or_the_official_images():
    text = "FROM ghcr.io/acme/base:2 AS build\nFROM build AS test\nFROM python:3.12\nFROM --platform=linux/amd64 acme/runtime\n"
    assert got("Dockerfile", text) == {("image", "ghcr.io/acme/base", "refers"), ("image", "acme/runtime", "refers")}


def test_a_ci_file_defines_the_images_it_builds_and_refers_to_the_repositories_it_uses():
    text = (
        "jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n"
        "      - uses: acme/shared-actions/.github/workflows/deploy.yml@v1\n      - uses: ./local-action\n"
        "      - with:\n          repository: acme/tools\n"
        "      - run: docker build -t ghcr.io/acme/payments:${{ github.sha }} .\n"
        "      - run: docker push ghcr.io/acme/payments:latest\n"
        "      - with:\n          tags: |\n            ghcr.io/acme/worker:1\n"
    )
    r = got(".github/workflows/build.yml", text)
    assert ("repo_ref", "github.com/acme/shared-actions", "refers") in r and (
        "repo_ref",
        "github.com/acme/tools",
        "refers",
    ) in r
    assert ("image", "ghcr.io/acme/payments", "defines") in r and ("image", "ghcr.io/acme/worker", "defines") in r
    assert not any("local-action" in v for _, v, _ in r)


def test_compose_defines_service_names_and_the_images_it_builds_and_refers_to_the_ones_it_pulls():
    text = (
        "services:\n  payments:\n    build: .\n    image: ghcr.io/acme/payments\n    environment:\n      - PAYMENTS_DB_URL=postgres://u:pw@db/x\n"
        "  cache:\n    image: redis:7\n  worker:\n    image: ghcr.io/acme/worker:2\nvolumes:\n  data:\n"
    )
    r = got("docker-compose.yml", text)
    assert (
        ("host", "payments", "defines") in r
        and ("host", "worker", "defines") in r
        and ("host", "data", "defines") not in r
    )
    assert ("image", "ghcr.io/acme/payments", "defines") in r and ("image", "ghcr.io/acme/worker", "refers") in r
    assert ("env", "PAYMENTS_DB_URL", "defines") in r and not any(v == "redis" for _, v, _ in r)


def test_kubernetes_and_helm_files_refer_to_images_and_define_services_hosts_and_variables():
    text = (
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: users\n---\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: payments\n"
        "spec:\n  containers:\n    - name: app\n      image: ghcr.io/acme/payments:1.4\n      env:\n        - name: PAYMENTS_DB_URL\n          value: x\n"
        "        - name: TEMPLATED\n          valueFrom:\n            secretKeyRef: {name: s}\n---\nkind: Ingress\nspec:\n  rules:\n    - host: shop.acme.io\n"
    )
    r = got("k8s/all.yaml", text)
    assert ("host", "users", "defines") in r and ("host", "shop.acme.io", "defines") in r
    assert ("image", "ghcr.io/acme/payments", "refers") in r
    assert ("env", "PAYMENTS_DB_URL", "defines") in r and ("env", "TEMPLATED", "defines") in r
    assert ("host", "payments", "defines") not in r  # a Deployment is not something a URL names
    helm = got(
        "chart/values.yaml",
        "image:\n  repository: ghcr.io/acme/web\n  tag: 1\nargo:\n  repoURL: https://github.com/acme/web-config.git\n",
    )
    assert helm == {("image", "ghcr.io/acme/web", "refers"), ("repo_ref", "github.com/acme/web-config", "refers")}
    assert got("chart/templates/d.yaml", "image: {{ .Values.image }}\n") == set()


def test_terraform_defines_queues_and_variables_and_refers_to_module_sources_and_images():
    text = (
        'module "payments" {\n  source = "git::https://github.com/acme/payments.git//deploy?ref=v1"\n}\n'
        'resource "aws_sqs_queue" "orders" {\n  name = "orders-created"\n}\n'
        'resource "aws_s3_bucket" "b" {\n  bucket = "acme-uploads"\n}\n'
        'resource "aws_sqs_queue" "dyn" {\n  name = "${var.prefix}-x"\n}\n'
        'resource "aws_lambda_function" "f" {\n  environment {\n    variables = {\n      PAYMENTS_DB_URL = var.db\n      lower = "x"\n    }\n  }\n}\n'
        'container_definitions = jsonencode([{ "image": "ghcr.io/acme/payments:1" }])\n'
    )
    r = got("terraform/main.tf", text)
    assert ("repo_ref", "github.com/acme/payments", "refers") in r
    assert ("queue", "orders-created", "defines") in r and ("queue", "acme-uploads", "defines") in r
    assert not any("prefix" in v for _, v, _ in r)  # a name built at deploy time is skipped, not guessed
    assert ("env", "PAYMENTS_DB_URL", "defines") in r and ("env", "lower", "defines") not in r
    assert ("image", "ghcr.io/acme/payments", "refers") in r


def test_scripts_define_what_they_build_and_refer_to_what_they_run_clone_and_call():
    text = (
        "#!/bin/sh\nexport PAYMENTS_DB_URL=$1\nexport PATH=/x\ndocker build -t ghcr.io/acme/tool:dev .\ndocker push ghcr.io/acme/tool:dev\n"
        "docker run --rm ghcr.io/acme/runner:1\ngit clone https://github.com/acme/lib.git /tmp/lib\ncurl -s https://api.acme.io/v1/ping\n"
    )
    r = got("scripts/deploy.sh", text)
    assert ("image", "ghcr.io/acme/tool", "defines") in r and ("image", "ghcr.io/acme/runner", "refers") in r
    assert ("repo_ref", "github.com/acme/lib", "refers") in r and ("host", "api.acme.io", "refers") in r
    assert ("env", "PAYMENTS_DB_URL", "defines") in r and not any(v == "PATH" for _, v, _ in r)


def test_submodules_and_example_environment_files():
    assert got(".gitmodules", '[submodule "x"]\n\turl = git@github.com:acme/shared.git\n') == {
        ("repo_ref", "github.com/acme/shared", "refers")
    }
    r = got(".env.example", "PAYMENTS_DB_URL=\nexport API=http://users:8080\nHOME=/root\n#X=1\n")
    assert (
        ("env", "PAYMENTS_DB_URL", "refers") in r
        and ("host", "users", "refers") in r
        and not any(v == "HOME" for _, v, _ in r)
    )


def test_application_code_refers_to_variables_it_reads_hosts_it_calls_and_names_it_uses():
    code = (
        'a = os.environ["PAYMENTS_DB_URL"]\nb = os.getenv("OTHER_KEY")\nc = process.env.WEB_TOKEN\nd = System.getenv("JAVA_KEY")\n'
        'e = ENV.fetch("RUBY_KEY")\nf = std::env::var("RUST_KEY")\ng = os.environ.get("HOME")\n'
        'requests.get("http://users:8080/x")\nrequests.get("http://other:1/x")\npublish("orders-created")\npublish("random")\n'
    )
    r = {(i.kind, i.value) for i in ids.code_refs(code, {"orders-created"}, {"users"}, True)}
    assert r == {
        ("env", "PAYMENTS_DB_URL"),
        ("env", "OTHER_KEY"),
        ("env", "WEB_TOKEN"),
        ("env", "JAVA_KEY"),
        ("env", "RUBY_KEY"),
        ("env", "RUST_KEY"),
        ("host", "users"),
        ("queue", "orders-created"),
    }
    assert ids.code_refs(code, set(), set(), False) == []  # nothing is defined, so nothing is looked for


def test_a_secret_in_a_url_or_a_value_never_becomes_part_of_a_name():
    text = (
        "env:\n  - name: DB\n    value: postgres://admin:s3cr3t-Passw0rd@db.internal:5432/x\n"
        "  - name: PAYMENTS_DB_URL\n    value: hunter2\n"
    )
    every = " ".join(f"{i.kind} {i.value} {i.note}" for i in ids.extract("k8s/a.yaml", text))
    assert "s3cr3t" not in every and "hunter2" not in every and "admin" not in every
    assert "db.internal" in every


def test_a_hostile_file_is_read_quickly_and_never_raises():
    start = time.monotonic()
    for path in (
        "k8s/a.yaml",
        "main.tf",
        "scripts/x.sh",
        "Dockerfile",
        "docker-compose.yml",
        ".github/workflows/a.yml",
    ):
        assert isinstance(ids.extract(path, "a" * 300_000 + "\n" + "- name: " * 20000 + "image: " * 20000), list)
    assert time.monotonic() - start < 15 and ids.extract("README.md", "# hi") == []


# --- links between repositories -----------------------------------------------------------------------------------

INFRA = {
    "k8s/payments.yaml": (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: payments\nspec:\n  containers:\n    - name: app\n"
        "      image: ghcr.io/acme/payments:1.4\n      env:\n        - name: PAYMENTS_DB_URL\n          value: postgres://admin:s3cr3t@db.internal/x\n"
    ),
    "k8s/users.yaml": "apiVersion: v1\nkind: Service\nmetadata:\n  name: users\n",
    "k8s/extra1.yaml": "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: one\n",
    "k8s/extra2.yaml": "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: two\n",
    "terraform/queues.tf": 'resource "aws_sqs_queue" "orders" {\n  name = "orders-created"\n}\n',
    "terraform/main.tf": 'module "payments" {\n  source = "git::https://github.com/acme/payments.git//deploy?ref=v1"\n}\n',
    "scripts/deploy.sh": "#!/bin/sh\nkubectl apply -f k8s/\n",
}
PAYMENTS = {
    "README.md": "# Payments\n",
    "payments/app.py": (
        'import os\nimport requests\n\n\ndef settings():\n    return os.environ["PAYMENTS_DB_URL"]\n\n\n'
        'def lookup(uid):\n    return requests.get(f"http://users:8080/users/{uid}").json()\n\n\n'
        'def announce(order):\n    publish("orders-created", order)\n'
    ),
    ".github/workflows/build.yml": "jobs:\n  b:\n    steps:\n      - run: docker build -t ghcr.io/acme/payments:${{ github.sha }} .\n",
}
UNRELATED = {
    "README.md": "# Other\n",
    "other/app.py": "import os\n\n\ndef home():\n    return os.environ['HOME'] + 'http://localhost:8000'\n",
    "Dockerfile": "FROM python:3.12\nCMD python app.py\n",
    "docker-compose.yml": "services:\n  cache:\n    image: redis:7\n  web:\n    image: nginx\n",
}


def world(client, auth, tmp_path, session):
    infra = add_repo(client, auth, make(tmp_path, "platform-infra", INFRA), name="platform-infra")
    pay = add_repo(client, auth, make(tmp_path, "payments-app", PAYMENTS), name="payments-app")
    other = add_repo(client, auth, make(tmp_path, "other-app", UNRELATED), name="other-app")
    row = session.get(Repository, pay["id"])
    row.web_url = "https://github.com/acme/payments"  # where the repository is hosted: how a module source finds it
    session.commit()
    rl.refresh(session, row.org_id, force=True)
    session.expire_all()
    return infra, pay, other


def pairs(session):
    return {
        (r.kind, r.rel, r.trust): (r.src_label, r.dst_label, r.evidence)
        for r in session.query(RepoLink).filter(RepoLink.kind.in_(ids.KINDS))
    }


def test_the_names_two_repositories_share_become_links_with_the_proof_on_both_ends(client, auth, tmp_path, session):
    infra, pay, other = world(client, auth, tmp_path, session)
    got_ = pairs(session)
    assert set(got_) == {
        ("image", "DEPLOYS", "fact"),
        ("repo_ref", "REFERENCES", "fact"),
        ("host", "CONNECTS", "inferred"),
        ("queue", "USES_QUEUE", "inferred"),
        ("env", "CONFIGURED", "uncertain"),
    }
    src, dst, ev = got_[("image", "DEPLOYS", "fact")]
    assert src == "k8s/payments.yaml:ghcr.io/acme/payments" and dst.endswith(":ghcr.io/acme/payments")
    assert "uses the image ghcr.io/acme/payments" in ev and "(a CI build or push step) builds that image" in ev
    assert ".github/workflows/build.yml" in ev
    row = session.query(RepoLink).filter_by(kind="repo_ref").one()
    assert (row.src_repo_id, row.dst_repo_id) == (
        infra["id"],
        pay["id"],
    ) and "refers to the repository github.com/acme/payments" in row.evidence
    host = session.query(RepoLink).filter_by(kind="host").one()
    assert (host.src_repo_id, host.dst_repo_id) == (pay["id"], infra["id"]) and "connects to users" in host.evidence


def test_a_repository_that_shares_no_name_is_not_linked_and_neither_are_the_names_every_project_has(
    client, auth, tmp_path, session
):
    infra, pay, other = world(client, auth, tmp_path, session)
    touched = {r.src_repo_id for r in session.query(RepoLink).filter(RepoLink.kind.in_(ids.KINDS))} | {
        r.dst_repo_id for r in session.query(RepoLink).filter(RepoLink.kind.in_(ids.KINDS))
    }
    assert other["id"] not in touched  # python, redis, nginx, localhost and HOME say nothing


def test_no_secret_reaches_a_stored_link(client, auth, tmp_path, session):
    world(client, auth, tmp_path, session)
    text = " ".join(f"{r.src_label} {r.dst_label} {r.evidence}" for r in session.query(RepoLink))
    assert "s3cr3t" not in text and "admin" not in text


def test_the_page_counts_names_as_links_except_a_variable_name_which_is_a_guess(client, auth, tmp_path, session):
    infra, pay, other = world(client, auth, tmp_path, session)
    a = client.get(
        "/api/groups/analysis", headers=auth, params={"repo_ids": f"{infra['id']},{pay['id']},{other['id']}"}
    ).json()
    by = {(x["src"], x["dst"]): x for x in a["links"]}
    out, back = by[(pay["id"], infra["id"])], by[(infra["id"], pay["id"])]
    assert out["infra"] == 2 and out["weak"] == 1 and {"CONNECTS", "USES_QUEUE", "CONFIGURED"} <= set(out["rels"])
    assert back["infra"] == 2 and back["trust"] == "fact" and {"DEPLOYS", "REFERENCES"} <= set(back["rels"])
    roles = {r["name"]: r["role"] for r in a["repos"]}
    assert roles == {"platform-infra": "infrastructure", "payments-app": "application", "other-app": "application"}


def test_the_code_graph_hides_the_guess_until_name_matches_are_asked_for(client, auth, tmp_path, session):
    infra, pay, other = world(client, auth, tmp_path, session)
    q = {"repo_ids": f"{infra['id']},{pay['id']}"}
    default = client.get("/api/groups/code-graph", headers=auth, params=q).json()
    rels = {e["rel"] for e in default["edges"]}
    assert {"DEPLOYS", "REFERENCES", "CONNECTS", "USES_QUEUE"} <= rels and "CONFIGURED" not in rels
    more = client.get("/api/groups/code-graph", headers=auth, params={**q, "weak": "true"}).json()
    assert "CONFIGURED" in {e["rel"] for e in more["edges"]} and default["available"]["weak"] >= 1


def test_a_name_defined_in_more_repositories_than_the_limit_is_too_ambiguous_to_link(
    client, auth, tmp_path, session, monkeypatch
):
    monkeypatch.setattr(rl, "MAX_SHARED_DEFINERS", 0)
    world(client, auth, tmp_path, session)
    assert pairs(session) == {}


def test_a_failing_finder_does_not_make_its_links_look_removed(client, auth, tmp_path, session, monkeypatch):
    infra, pay, other = world(client, auth, tmp_path, session)
    before = session.query(RepoLink).filter(RepoLink.kind.in_(ids.KINDS), RepoLink.gone_at.is_(None)).count()

    def boom(self, db, repo_ids):
        raise RuntimeError("detector crashed")

    monkeypatch.setattr(rl.IdentifierDetector, "detect", boom)
    state = rl.refresh(session, session.get(Repository, pay["id"]).org_id, force=True)
    session.expire_all()
    assert session.query(RepoLink).filter(RepoLink.kind.in_(ids.KINDS), RepoLink.gone_at.is_(None)).count() == before
    assert set(ids.KINDS) <= set(state.coverage["_failed"])


def test_who_deploys_a_repository_is_shown_in_its_surface_and_in_blast_radius(client, auth, tmp_path, session):
    infra, pay, other = world(client, auth, tmp_path, session)
    s = client.get("/api/groups/surface", headers=auth, params={"repo_id": pay["id"]}).json()
    assert {(d["repo"], d["rel"]) for d in s["linked_by"]} == {
        ("platform-infra", "DEPLOYS"),
        ("platform-infra", "REFERENCES"),
    }
    assert all(x["type"] != "file" for x in s["symbols"])
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": f"{infra['id']},{pay['id']}"}).json()
    node = next(n for n in g["nodes"] if n["path"] == ".github/workflows/build.yml")
    out = client.get("/api/groups/impact", headers=auth, params={"node_id": node["id"]}).json()
    assert [d["rel"] for d in out["direct"]] == ["DEPLOYS"] and out["direct"][0]["repo"] == "platform-infra"


def test_a_repository_someone_cannot_open_is_counted_never_named_in_these_links(client, auth, tmp_path, session):
    infra, pay, other = world(client, auth, tmp_path, session)
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.put(
        f"/api/repositories/{infra['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []}
    )
    s = client.get("/api/groups/surface", headers=ann, params={"repo_id": pay["id"]}).json()
    assert s["linked_by"] == [] and s["hidden"] >= 2 and "platform-infra" not in str(s)


def test_a_new_name_after_a_reindex_is_reported_as_a_new_dependency(client, auth, tmp_path, session):
    infra, pay, other = world(client, auth, tmp_path, session)
    ids_ = f"{infra['id']},{pay['id']}"
    client.get("/api/groups/changes", headers=auth, params={"repo_ids": ids_})  # tracking starts
    path = session.get(Repository, infra["id"]).url
    with open(f"{path}/k8s/worker.yaml", "w") as f:
        f.write("kind: Deployment\nspec:\n  containers:\n    - image: ghcr.io/acme/payments:2.0\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=d", "-c", "user.email=d@x.io", "commit", "-qm", "worker"], check=True
    )
    assert client.post(f"/api/repositories/{infra['id']}/index", headers=auth).status_code == 202
    r = client.get("/api/groups/changes", headers=auth, params={"repo_ids": ids_}).json()
    assert [(p["from"], p["to"]) for p in r["new"]] == [("platform-infra", "payments-app")]
    assert "k8s/worker.yaml" in r["new"][0]["examples"][0]["from"]
