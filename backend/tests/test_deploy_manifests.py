"""The deployment files (Kubernetes, Argo CD, HTTPS) are code too: they must parse and agree with each other."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
pytestmark = pytest.mark.skipif(not DEPLOY.exists(), reason="deploy files are not part of the backend image")


class ComposeLoader(yaml.SafeLoader):
    """Docker Compose adds tags such as `!override` and `!reset`; read them as the plain value they wrap."""


ComposeLoader.add_multi_constructor(
    "!",
    lambda loader, suffix, node: (
        loader.construct_sequence(node, deep=True)
        if isinstance(node, yaml.SequenceNode)
        else loader.construct_scalar(node)
    ),
)


def load(path: Path) -> list[dict]:
    return [d for d in yaml.load_all(path.read_text(), Loader=ComposeLoader) if d]


def test_every_deployment_file_is_valid_yaml():
    files = [p for p in list(DEPLOY.rglob("*.yaml")) + [ROOT / "docker-compose.tls.yml"]]
    assert len(files) >= 12
    for p in files:
        load(p)  # raises on a syntax error; the Caddyfile is not YAML and is checked separately


def test_argo_cd_application_follows_the_gitops_rules():
    app = load(DEPLOY / "argocd" / "application.yaml")[0]
    assert app["kind"] == "Application" and app["apiVersion"] == "argoproj.io/v1alpha1"
    spec = app["spec"]
    assert (ROOT / spec["source"]["path"] / "kustomization.yaml").exists()  # it points at a real overlay
    assert spec["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}
    assert spec["destination"]["namespace"] == "repolens"
    ignored = {d["name"] for d in spec["ignoreDifferences"]}
    assert ignored == {"web", "api"}  # the autoscaler owns replica counts, so a self-healing sync must not fight it
    assert "Secret" not in str(app)  # secrets are never part of the application


def test_the_production_overlay_changes_images_and_host_without_touching_the_base():
    k = load(DEPLOY / "overlays" / "production" / "kustomization.yaml")[0]
    assert k["resources"] == ["../../k8s"]
    assert {i["name"] for i in k["images"]} == {"ghcr.io/OWNER/repolens-api", "ghcr.io/OWNER/repolens-web"}
    assert all(i["newName"].startswith("ghcr.io/") and i["newTag"] for i in k["images"])
    patched = "\n".join(p["patch"] for p in k["patches"])
    assert (
        "/spec/rules/0/host" in patched and "REPOLENS_CORS_ORIGINS" in patched
    )  # the host name is changed everywhere it appears
    assert not any("secret" in p.lower() for p in map(str, k.get("resources", [])))  # no secret in git


def test_the_ingress_uses_the_tls_secret_that_both_certificate_routes_fill():
    ingress = next(d for d in load(DEPLOY / "k8s" / "web.yaml") if d["kind"] == "Ingress")
    assert (
        ingress["spec"]["tls"][0]["secretName"] == "repolens-tls"
    )  # `kubectl create secret tls repolens-tls`, or cert-manager
    assert (
        ingress["metadata"]["annotations"]["nginx.ingress.kubernetes.io/proxy-body-size"] == "101m"
    )  # the default (1 MB) would refuse imports
    assert (
        "client_max_body_size 101m" in (ROOT / "frontend" / "nginx.conf").read_text()
    )  # the web container allows what the API allows
    comp = load(DEPLOY / "components" / "cert-manager" / "kustomization.yaml")[0]
    assert comp["kind"] == "Component" and "cert-manager.io~1cluster-issuer" in comp["patches"][0]["patch"]
    issuer = load(DEPLOY / "extras" / "cluster-issuer-letsencrypt.yaml")[0]
    assert (
        issuer["kind"] == "ClusterIssuer" and issuer["metadata"]["name"] == "letsencrypt"
    )  # the name the component refers to


def test_compose_https_hides_plain_http_and_never_has_a_default_domain():
    c = load(ROOT / "docker-compose.tls.yml")[0]
    assert c["services"]["web"]["ports"] == [
        "127.0.0.1:${WEB_PORT:-8080}:8080"
    ]  # plain HTTP is no longer reachable from outside
    assert (
        c["services"]["api"]["environment"]["REPOLENS_TRUSTED_PROXY_HOPS"] == "2"
    )  # otherwise every user shares one sign-in limit
    assert c["services"]["caddy"]["ports"] == ["80:80", "443:443"]
    assert ":?" in c["services"]["caddy"]["environment"]["REPOLENS_DOMAIN"]  # refuses to start without a host name
    caddy = (DEPLOY / "caddy" / "Caddyfile").read_text()
    assert "reverse_proxy web:8080" in caddy and "Strict-Transport-Security" in caddy


def test_cd_skips_cluster_access_in_gitops_mode():
    cd = yaml.safe_load((ROOT / ".github" / "workflows" / "cd.yml").read_text())
    assert "gitops" in cd["jobs"]["deploy"]["if"]
