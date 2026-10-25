import re
import tomllib
from pathlib import Path

import pytest

from repolens import VERSION_LABEL, __version__

ROOT = Path(__file__).resolve().parents[2]


def test_the_release_is_v0_1_beta_and_reported_by_the_health_check(client):
    assert VERSION_LABEL == "v0.1-beta" and __version__ == "0.1.0b0"
    assert client.get("/healthz").json() == {"status": "ok", "version": "v0.1-beta"}


def test_every_place_that_names_the_version_agrees():
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text())["project"]["version"]
    assert pyproject == __version__
    web = ROOT / "frontend" / "src" / "lib" / "version.ts"
    if not web.exists():  # the backend image is built without the frontend
        pytest.skip("frontend sources not present")
    assert re.search(r'APP_VERSION = "([^"]+)"', web.read_text()).group(1) == VERSION_LABEL
    pkg = re.search(r'"version": "([^"]+)"', (ROOT / "frontend" / "package.json").read_text()).group(1)
    assert pkg == "0.1.0-beta"
