# Operations: backups, restore, upgrades

## What to back up

Everything RepoLens remembers is in PostgreSQL: users, sessions, repositories, the search index, the knowledge graph, wiki pages,
investigations, the answer memory, settings and connection secrets (encrypted). Git clones under `/data/repos` are a cache and are
rebuilt from the remote on the next index. Keep a copy of your secrets (`REPOLENS_SECRET_KEY`, `REPOLENS_JWT_SECRET`) somewhere safe
and separate: **encrypted connection secrets in a restored database cannot be read without the same `REPOLENS_SECRET_KEY`**.

## Kubernetes

`deploy/k8s/backup.yaml` adds a nightly `CronJob` (02:15 UTC) that writes a compressed dump (`pg_dump -Fc`) to its own volume
`repolens-backups`, checks the file can be read back (`pg_restore --list`), and deletes dumps older than 14 days
(`RETENTION_DAYS`). It runs with the same restricted security settings as the other pods and may only talk to PostgreSQL.

- Run one now: `kubectl -n repolens create job backup-now --from=cronjob/postgres-backup && kubectl -n repolens logs -f job/backup-now`
- A backup on the same disk as the database does not survive losing that disk. Copy the dumps off the cluster (object storage, a
  backup agent, or `kubectl cp` from a pod that mounts `repolens-backups`). For a managed PostgreSQL, use its own snapshots and
  point-in-time recovery instead and drop this job from `kustomization.yaml`.

### Restore

1. Stop writes: `kubectl -n repolens scale deploy/api --replicas=0`.
2. Restore into a new database first and check it:
   ```bash
   # a throwaway pod that mounts the backups volume and has the postgres tools
   kubectl -n repolens run restore --rm -it --restart=Never --image=pgvector/pgvector:pg16 \
     --overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":999},"containers":[{"name":"restore","image":"pgvector/pgvector:pg16","stdin":true,"tty":true,"command":["bash"],"env":[{"name":"PGPASSWORD","valueFrom":{"secretKeyRef":{"name":"repolens-secrets","key":"POSTGRES_PASSWORD"}}}],"volumeMounts":[{"name":"b","mountPath":"/backups","readOnly":true},{"name":"t","mountPath":"/tmp"}]}],"volumes":[{"name":"b","persistentVolumeClaim":{"claimName":"repolens-backups"}},{"name":"t","emptyDir":{}}]}}'
   # inside it (the pod needs the label app=backup to reach the database; add --labels=app=backup to the command above)
   ls /backups
   psql -h postgres -U repolens -d postgres -c 'create database repolens_restore'
   pg_restore -h postgres -U repolens -d repolens_restore --no-owner /backups/repolens-YYYYMMDDTHHMMSSZ.dump
   psql -h postgres -U repolens -d repolens_restore -c 'select count(*) from users'
   ```
3. To replace the live data: drop and recreate the `repolens` database (or restore into it with `--clean --if-exists`), then
   `kubectl -n repolens scale deploy/api --replicas=1`. The API applies any newer migrations when it starts.
4. Open the app, sign in, and open a repository and a wiki page. Then drop `repolens_restore`.

Rehearse a restore once now and after any big change; an untested backup is a hope, not a backup. This procedure was run end to end
on a real cluster when the job was added (dump, read-back check, restore of the data into a new database).

## Docker Compose and local runs

```bash
make backup                       # writes backups/repolens-<time>.dump from the compose database
make restore file=backups/repolens-20261025T021500Z.dump   # restores into a new database named repolens_restore
```

## Logs: size and rotation

Logs are JSON lines. They are written as they happen and never kept in memory, so they cost disk, not RAM (the server's own
in-memory history is a few hundred small samples for the Insights page).

- **What is logged:** application events, warnings and errors, and one line per API request: `METHOD /path status time caller`
  (about 100 bytes). The query string is never logged, because it can contain what someone searched for. Health checks and the screens
  that poll the server every few seconds are logged only when they fail. The HTTP client's own request lines are off for the same
  reason. At a steady 10 requests per second that is roughly 85 MB a day of access lines before rotation.
- **Bounding it:** set `REPOLENS_LOG_FILE=/path/repolens.log` and the file rotates at `REPOLENS_LOG_MAX_MB` (10) keeping
  `REPOLENS_LOG_BACKUPS` (5) older files, so at most 60 MB; only warnings and errors then go to standard output. Without a log file,
  everything goes to standard output: Docker Compose caps it at 5 files of 10 MB per container (`docker-compose.yml`), and Kubernetes
  rotates container logs itself (the kubelet defaults to 10 MiB and 5 files per container). Ship logs to a central system if you
  need more than that history.
- `./run-local.sh` writes `.run/repolens.log` (rotating); `.run/api.log` holds only start-up output and errors.
- The database also grows: the daily retention sweep below keeps audit events, feedback and old sessions bounded.

## Retention and deleting data

A daily job removes what is no longer useful: remembered answers past their lifetime, feedback older than 365 days, audit events older
than 730 days, ended sign-ins and used or expired invitation links older than 30 days. Change the periods with
`REPOLENS_RETENTION_FEEDBACK_DAYS`, `_AUDIT_DAYS`, `_SESSIONS_DAYS` and `_INVESTIGATION_DAYS` (0 keeps that kind forever; investigations
are kept until someone deletes them unless you set a period). Admins can run it immediately from the API
(`POST /api/settings/retention/run`).

To delete a whole workspace (a test one, or for a deletion request): `python -m repolens.devtools.delete_workspace --email <any person
in it>` shows what is there; add `--yes` to delete. Take a backup first. It cannot be undone.

## Upgrading

Images run `alembic upgrade head` on start, so deploying a new image migrates the database. Take a backup first
(`kubectl create job --from=cronjob/postgres-backup`), and read the **Upgrade notes** in `changelog/` for the versions you skip.
Changing the embedding model needs `python -m repolens.devtools.reembed` (see `evals/README.md`).

## Versions and releases
The app is at **v0.1-beta**. The number lives in `backend/repolens/__init__.py` (`VERSION_LABEL`, with the PEP 440 spelling `0.1.0b0` for packaging), `frontend/src/lib/version.ts` and `frontend/package.json`; `tests/test_version.py` fails if they disagree. `GET /healthz` returns it (`{"status": "ok", "version": "v0.1-beta"}`), which is the quickest way to see what a server runs. The deploy workflow (`cd.yml`) only starts on tags shaped like `vMAJOR.MINOR.PATCH` (for example `v0.1.0`), so a beta tag such as `v0.1-beta` publishes a GitHub release without deploying anything.
