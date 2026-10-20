"""Builds a small demo git repository with a realistic regression, for seeding and tests."""

import os
import subprocess
from pathlib import Path

FILES_V1 = {
    "src/repository.py": '''"""User persistence layer."""


class UserRepository:
    def __init__(self, db):
        self.db = db

    def find_by_id(self, user_id):
        row = self.db.get(user_id)
        return row  # may be None for deleted users
''',
    "src/user_profile.py": '''"""Profile service."""
from .repository import UserRepository


class UserProfileService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    def get_profile(self, user_id):
        user = self.repo.find_by_id(user_id)
        if user is None or user.get("metadata") is None:
            return {"id": user_id, "display_name": "Unknown"}
        return {"id": user_id, "display_name": user["metadata"]["display_name"]}
''',
    "src/api.py": """from .user_profile import UserProfileService


def handle_profile_request(service: UserProfileService, user_id):
    profile = service.get_profile(user_id)
    return {"status": 200, "body": profile}
""",
    "README.md": (
        "# user-service\n\nProfile API for the platform.\n\nSee the [architecture guide](docs/architecture.md).\n"
    ),
    "docs/architecture.md": """# Architecture

The service has three layers: API handler, profile service and repository.

## Profile lookup

`UserProfileService.get_profile` loads the user through `UserRepository.find_by_id` and builds the response.
The HTTP entry point is described in [the API handler](../src/api.py); see also the [README](../README.md).

## Deleted users

A deleted user must still get a response: the profile service returns a default profile whose display name is
"Unknown". The behaviour is exercised by `tests/test_user_profile.py` and handled in `handle_profile_request`.
""",
    "tests/test_user_profile.py": """from src.user_profile import UserProfileService


class FakeRepo:
    def find_by_id(self, user_id):
        return None


def test_deleted_user_gets_default_profile():
    service = UserProfileService(FakeRepo())
    assert service.get_profile(1)["display_name"] == "Unknown"
""",
}

FILES_V2 = dict(FILES_V1)
FILES_V2["src/user_profile.py"] = '''"""Profile service."""
from .repository import UserRepository


class UserProfileService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    def get_profile(self, user_id):
        user = self.repo.find_by_id(user_id)
        return {"id": user_id, "display_name": user["metadata"]["display_name"]}
'''

BUG_REPORT = """Null pointer exception in user profile service after deploy.
Fetching the profile of a deleted user returns HTTP 500.

Traceback (most recent call last):
  File "src/api.py", line 5, in handle_profile_request
    profile = service.get_profile(user_id)
  File "src/user_profile.py", line 11, in get_profile
    return {"id": user_id, "display_name": user["metadata"]["display_name"]}
TypeError: 'NoneType' object is not subscriptable
"""


def _run(path: Path, *args: str, env_extra: dict | None = None) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Dev One",
        "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "Dev One",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
        **(env_extra or {}),
    }
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, env=env)


def _write(path: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)


def build_demo_repo(path: Path) -> str:
    """Create the repo and return the SHA of the commit that introduced the regression."""
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q", "-b", "main")
    _write(path, FILES_V1)
    _run(path, "add", "-A")
    _run(
        path,
        "commit",
        "-q",
        "-m",
        "feat: user profile service with repository layer",
        env_extra={"GIT_AUTHOR_DATE": "2026-09-01T10:00:00Z", "GIT_COMMITTER_DATE": "2026-09-01T10:00:00Z"},
    )
    _write(path, {"README.md": "# user-service\n\nProfile API for the platform.\n\nRun with `make run`.\n"})
    _run(path, "add", "-A")
    _run(
        path,
        "commit",
        "-q",
        "-m",
        "docs: describe how to run the service",
        env_extra={"GIT_AUTHOR_DATE": "2026-09-10T10:00:00Z", "GIT_COMMITTER_DATE": "2026-09-10T10:00:00Z"},
    )
    _write(path, FILES_V2)
    _run(path, "add", "-A")
    _run(
        path,
        "commit",
        "-q",
        "-m",
        "refactor: simplify profile metadata lookup in user profile service",
        env_extra={"GIT_AUTHOR_DATE": "2026-10-14T10:00:00Z", "GIT_COMMITTER_DATE": "2026-10-14T10:00:00Z"},
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
