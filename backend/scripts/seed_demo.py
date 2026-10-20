"""Seed a demo organization, repository and investigation against a running API.

python scripts/seed_demo.py --api http://localhost:8000 --email demo@example.com
"""

import argparse
import tempfile
import time
from pathlib import Path

import httpx

from repolens.demo import BUG_REPORT, build_demo_repo

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://localhost:8000")
ap.add_argument("--email", default="demo@example.com")
ap.add_argument("--password", default="demo-password-123")
ap.add_argument("--path", default=str(Path(tempfile.gettempdir()) / "repolens-demo" / "user-service"))
a = ap.parse_args()

path = Path(a.path)
if not (path / ".git").exists():
    build_demo_repo(path)
c = httpx.Client(base_url=a.api, timeout=30)
r = c.post("/api/auth/register", json={"email": a.email, "password": a.password, "organization": "Demo"})
if r.status_code == 409:
    r = c.post("/api/auth/login", json={"email": a.email, "password": a.password})
r.raise_for_status()
c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
repo = c.post("/api/repositories", json={"name": "user-service", "source": "local", "url": str(path)})
if repo.status_code == 409:
    repo = [x for x in c.get("/api/repositories").json() if x["name"] == "user-service"][0]
    rid = repo["id"]
else:
    repo.raise_for_status()
    rid = repo.json()["id"]
for _ in range(60):
    if c.get(f"/api/repositories/{rid}").json()["status"] == "ready":
        break
    time.sleep(1)
inv = c.post("/api/investigations", json={"query": BUG_REPORT, "repo_ids": [rid]})
inv.raise_for_status()
print("investigation:", inv.json()["id"], "- login:", a.email, a.password)
