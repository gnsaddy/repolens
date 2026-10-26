# HTTPS certificates and Argo CD deployment

### Added
- **HTTPS guide** (`docs/operations.md`): bring your own certificate with one `kubectl create secret tls` command, or get automatic certificates with cert-manager (`deploy/components/cert-manager`, `deploy/extras/cluster-issuer-letsencrypt.yaml`), or put Caddy in front of Docker Compose (`docker-compose.tls.yml`, `deploy/caddy/Caddyfile`). Includes how to check it and why the number of trusted proxy hops matters for sign-in rate limits.
- **Argo CD (GitOps) layout**: `deploy/argocd/application.yaml` (auto-sync, prune, self-heal, autoscaler-owned replicas ignored), a production overlay (`deploy/overlays/production`) that sets release tag, host name and CORS origin, and a configuration-repository example.
- Repository variable `DEPLOY_MODE=gitops` makes the CD workflow publish images only, with no cluster credentials.
- CI builds the production overlay and parses the Argo CD application; `tests/test_deploy_manifests.py` checks the files agree with each other.
