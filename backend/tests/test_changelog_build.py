import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "changelog" / "build_index.py"


@pytest.fixture
def cl(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("changelog_build", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "backend" / "repolens").mkdir(parents=True)
    (tmp_path / "changelog" / "unreleased").mkdir(parents=True)
    set_version(tmp_path, "v1-beta")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "HERE", tmp_path / "changelog")
    monkeypatch.setattr(mod, "UNRELEASED", tmp_path / "changelog" / "unreleased")
    return mod


def set_version(root, label):
    (root / "backend" / "repolens" / "__init__.py").write_text(f'VERSION_LABEL = "{label}"\n')


def draft(root, name, title, body="### Added\n- a thing\n"):
    (root / "changelog" / "unreleased" / name).write_text(f"# {title}\n\n{body}")


def test_drafts_are_folded_by_date_newest_first_and_removed(cl, tmp_path):
    draft(tmp_path, "2026-10-25-older.md", "Older change")
    draft(tmp_path, "2026-10-26-newer.md", "Newer change", "### Fixed\n- a bug\n")
    label, n = cl.fold()
    text = (tmp_path / "changelog" / "v1-beta.md").read_text()
    assert (label, n) == ("v1-beta", 2)
    assert text.index("## 2026-10-26") < text.index("## 2026-10-25")
    assert "### Newer change" in text and "#### Fixed" in text  # the draft's own sections sit one level down
    assert "- a bug" in text
    assert not list((tmp_path / "changelog" / "unreleased").glob("*.md"))


def test_folding_twice_changes_nothing_and_new_entries_lead_their_day(cl, tmp_path):
    draft(tmp_path, "2026-10-26-first.md", "First")
    cl.fold()
    once = (tmp_path / "changelog" / "v1-beta.md").read_text()
    assert cl.fold()[1] == 0
    assert (tmp_path / "changelog" / "v1-beta.md").read_text() == once
    draft(tmp_path, "2026-10-26-second.md", "Second")
    cl.fold()
    text = (tmp_path / "changelog" / "v1-beta.md").read_text()
    assert text.index("### Second") < text.index("### First")
    assert text.count("## 2026-10-26") == 1


def test_a_new_version_label_starts_a_new_file_and_the_index_lists_both(cl, tmp_path):
    draft(tmp_path, "2026-10-25-a.md", "A")
    cl.fold()
    set_version(tmp_path, "v2-beta")
    draft(tmp_path, "2026-10-27-b.md", "B")
    cl.fold()
    assert "### A" in (tmp_path / "changelog" / "v1-beta.md").read_text()
    assert "### B" not in (tmp_path / "changelog" / "v1-beta.md").read_text()
    assert "### B" in (tmp_path / "changelog" / "v2-beta.md").read_text()
    assert cl.write_index() == 2
    readme = (tmp_path / "changelog" / "README.md").read_text()
    assert readme.index("v2-beta") < readme.index("v1-beta")  # newest version first
    assert "1 entries" in readme


def test_parse_round_trips_a_rendered_file(cl):
    days = {"2026-10-26": [("T2", "#### Added\n- x"), ("T1", "- y")], "2026-10-25": [("T0", "- z")]}
    assert cl.parse(cl.render("v1", days)) == days
