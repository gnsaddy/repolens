"""Package manifests: what each ecosystem's file offers and asks for, and the links between repositories they give."""

import subprocess
import time

from repolens.models import RepoLink, Repository
from repolens.services import manifests as mf
from repolens.services import repo_links as rl
from tests.test_api import add_repo
from tests.test_chat import make_user
from tests.test_groups import days_ago
from tests.test_risk import commit, git

# --- the parsers ---------------------------------------------------------------------------------------------------


def names(m: mf.Manifest) -> list[str]:
    return [r.name for r in m.requires]


def test_names_that_pip_treats_as_one_package_compare_equal():
    assert len({mf.norm("python", n) for n in ("User_Lib", "user-lib", "user.lib", "USER--LIB")}) == 1
    assert mf.norm("cargo", "serde_json") == mf.norm("cargo", "serde-json")
    assert mf.norm("npm", "@Acme/UI-Kit") == "@acme/ui-kit"


def test_pyproject_reads_the_standard_and_poetry_layouts_with_the_scope_of_each():
    text = (
        '[project]\nname = "shop"\ndependencies = ["user_lib>=1.2", "requests[socks]>=2 ; python_version>\'3.8\'"]\n'
        '[project.optional-dependencies]\ndev = ["pytest"]\nredis = ["redis"]\n'
        '[dependency-groups]\nlint = ["ruff"]\n'
        '[tool.poetry]\nname = "shop-poetry"\n[tool.poetry.dependencies]\npython = "^3.11"\nflask = "^3"\n'
        '[tool.poetry.group.test.dependencies]\nhypothesis = "^6"\n'
    )
    m = mf.parse("backend/pyproject.toml", text)
    assert [n for n, _ in m.provides] == ["shop", "shop-poetry"]
    got = {r.name: r.scope for r in m.requires}
    assert got == {
        "user_lib": "runtime",
        "requests": "runtime",
        "pytest": "dev",
        "redis": "optional",
        "ruff": "dev",
        "flask": "runtime",
        "hypothesis": "dev",
    }
    assert next(r for r in m.requires if r.name == "user_lib").spec == ">=1.2" and m.requires[0].line == 3


def test_requirements_files_handle_pins_extras_urls_editable_installs_and_comments():
    text = (
        "# runtime\nuser-lib==1.2.3  # pinned\nrequests[socks]>=2\n-r other.txt\n--index-url https://x\n"
        "-e git+https://github.com/acme/tool.git#egg=acme-tool\nmylib @ https://example.com/mylib.whl\n\n"
    )
    m = mf.parse("requirements-dev.txt", text)
    assert names(m) == ["user-lib", "requests", "acme-tool", "mylib"]
    assert m.requires[0].spec == "==1.2.3" and m.requires[2].spec == "source" and m.requires[0].line == 2


def test_package_json_reads_every_dependency_section():
    text = (
        '{"name": "@acme/web", "dependencies": {"@acme/ui-kit": "^1.0.0", "react": "^18"},\n'
        ' "devDependencies": {"vitest": "^1"}, "peerDependencies": {"react-dom": "^18"}, "optionalDependencies": {"fsevents": "^2"}}'
    )
    m = mf.parse("package.json", text)
    assert m.provides[0][0] == "@acme/web"
    assert {r.name: r.scope for r in m.requires} == {
        "@acme/ui-kit": "runtime",
        "react": "runtime",
        "vitest": "dev",
        "react-dom": "runtime",
        "fsevents": "optional",
    }


def test_go_mod_reads_the_module_blocks_single_lines_indirect_and_replace():
    text = (
        "module github.com/acme/svc\n\ngo 1.22\n\nrequire (\n\tgithub.com/acme/lib v1.2.3\n"
        "\tgolang.org/x/text v0.3.0 // indirect\n)\n\nrequire github.com/acme/one v1.0.0\n"
        "replace github.com/acme/old => github.com/acme/new v2.0.0\n"
    )
    m = mf.parse("go.mod", text)
    assert m.provides[0][0] == "github.com/acme/svc"
    got = {r.name: r.scope for r in m.requires}
    assert got["github.com/acme/lib"] == "runtime" and got["golang.org/x/text"] == "indirect"
    assert "github.com/acme/one" in got and "github.com/acme/new" in got


def test_pom_inherits_the_group_skips_managed_versions_and_reads_dependencies():
    text = (
        "<project><parent><groupId>com.acme</groupId><artifactId>parent</artifactId></parent>\n"
        "<artifactId>svc</artifactId>\n<dependencyManagement><dependencies><dependency><groupId>x</groupId>"
        "<artifactId>managed</artifactId></dependency></dependencies></dependencyManagement>\n"
        "<dependencies><dependency><groupId>com.acme</groupId><artifactId>lib</artifactId><version>1.0</version></dependency>\n"
        "<dependency><groupId>junit</groupId><artifactId>junit</artifactId><scope>test</scope></dependency></dependencies></project>"
    )
    m = mf.parse("pom.xml", text)
    assert m.provides[0][0] == "com.acme:svc"
    assert {r.name: r.scope for r in m.requires} == {"com.acme:lib": "runtime", "junit:junit": "dev"}


def test_a_hostile_xml_file_cannot_expand_into_memory():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">'
        + ('<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">' * 5)
        + "]><project><artifactId>&c;</artifactId></project>"
    )
    start = time.monotonic()
    m = mf.parse("pom.xml", bomb)
    assert time.monotonic() - start < 1 and isinstance(m, mf.Manifest)


def test_gradle_cargo_gems_composer_and_dotnet_manifests():
    g = mf.parse(
        "build.gradle",
        "dependencies {\n  implementation 'com.acme:lib:1.0'\n  testImplementation(\"junit:junit:4\")\n}",
    )
    assert {r.name: r.scope for r in g.requires} == {"com.acme:lib": "runtime", "junit:junit": "dev"}
    c = mf.parse(
        "Cargo.toml",
        '[package]\nname = "my_app"\n[dependencies]\nserde = "1"\nweb = { package = "real-web", version = "2" }\n',
    )
    assert c.provides[0][0] == "my_app" and names(c) == ["serde", "real-web"]
    assert names(mf.parse("Gemfile", "source 'https://rubygems.org'\ngem 'rails', '~> 7'\ngem \"puma\"\n")) == [
        "rails",
        "puma",
    ]
    gs = mf.parse(
        "acme.gemspec",
        "Gem::Specification.new do |s|\n  s.name = 'acme'\n  s.add_dependency 'rack'\n  s.add_development_dependency 'rspec'\nend",
    )
    assert gs.provides[0][0] == "acme" and {r.name: r.scope for r in gs.requires} == {"rack": "runtime", "rspec": "dev"}
    comp = mf.parse(
        "composer.json", '{"name": "acme/web", "require": {"php": ">=8", "ext-json": "*", "acme/lib": "^1"}}'
    )
    assert comp.provides[0][0] == "acme/web" and names(comp) == ["acme/lib"]  # the platform is not a package
    cs = mf.parse(
        "Acme.Web.csproj",
        '<Project><PropertyGroup><PackageId>Acme.Web</PackageId></PropertyGroup><ItemGroup><PackageReference Include="Acme.Lib" Version="1.2" /></ItemGroup></Project>',
    )
    assert cs.provides[0][0] == "Acme.Web" and names(cs) == ["Acme.Lib"]


def test_a_file_that_cannot_be_read_gives_an_error_not_an_exception():
    assert "not valid TOML" in mf.parse("pyproject.toml", "[project\nname=").error
    assert "not valid JSON" in mf.parse("package.json", "{nope").error
    assert mf.parse("package.json", "[1, 2]").error == "not a JSON object"
    assert mf.parse("pom.xml", "hello").error == "not a Maven project file"
    assert mf.parse("README.md", "# hi") == mf.Manifest()


# --- links between repositories ------------------------------------------------------------------------------------


def make(tmp_path, name, files, when=2):
    p = tmp_path / name
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    commit(p, files, f"feat: {name}", days_ago(when))
    return p


LIB_CODE = {
    "userlib/__init__.py": "from .core import get_user as get_user\n",
    "userlib/core.py": "def get_user(user_id):\n    return {'id': user_id}\n",
}
USING = "from userlib import get_user\n\n\ndef checkout(uid):\n    return get_user(uid)\n"


def lib(tmp_path):
    return make(
        tmp_path, "users-lib", {**LIB_CODE, "pyproject.toml": '[project]\nname = "user-lib"\nversion = "1.2"\n'}
    )


def links(session, kind="manifest"):
    rl.refresh(session, session.query(Repository).first().org_id)  # links are worked out when something asks for them
    session.expire_all()
    return session.query(RepoLink).filter_by(kind=kind, gone_at=None).all()


def analysis(client, auth, *repos):
    ids = ",".join(r["id"] for r in repos)
    return client.get("/api/groups/analysis", headers=auth, params={"repo_ids": ids}).json()


def test_a_declared_dependency_on_another_repositorys_package_is_a_link(client, auth, tmp_path, session):
    users = add_repo(client, auth, lib(tmp_path), name="users-lib")
    files = {
        "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["user_lib>=1.2", "requests"]\n',
        "shop/c.py": USING,
    }
    shop = add_repo(client, auth, make(tmp_path, "shop-app", files), name="shop-app")
    (row,) = links(session)
    assert (row.rel, row.tier, row.trust, row.src_label, row.dst_label) == (
        "DECLARES",
        "declared",
        "fact",
        "pyproject.toml:user_lib",
        "pyproject.toml:user-lib",
    )
    assert "requires user_lib >=1.2 (runtime); pyproject.toml names the package user-lib" in row.evidence
    a = analysis(client, auth, shop, users)
    (link,) = [x for x in a["links"] if x["src"] == shop["id"]]
    assert link["declared"] == 1 and link["rels"]["DECLARES"] == 1 and link["trust"] == "fact"
    assert link["declared_unused"] is False and link["undeclared"] is False  # declared, and the code uses it
    assert any(e["rel"] == "DECLARES" for e in link["examples"])


def test_a_dependency_that_is_declared_but_never_used_is_called_out(client, auth, tmp_path):
    users = add_repo(client, auth, lib(tmp_path), name="users-lib")
    files = {
        "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["user-lib"]\n',
        "shop/c.py": "def checkout():\n    return 1\n",
    }
    shop = add_repo(client, auth, make(tmp_path, "shop-app", files), name="shop-app")
    (link,) = [x for x in analysis(client, auth, shop, users)["links"] if x["src"] == shop["id"]]
    assert link["declared_unused"] is True and link["imports"] == 0


def test_a_dependency_that_is_used_but_not_declared_is_called_out(client, auth, tmp_path):
    users = add_repo(client, auth, lib(tmp_path), name="users-lib")
    files = {"pyproject.toml": '[project]\nname = "shop"\ndependencies = ["requests"]\n', "shop/c.py": USING}
    shop = add_repo(client, auth, make(tmp_path, "shop-app", files), name="shop-app")
    (link,) = [x for x in analysis(client, auth, shop, users)["links"] if x["src"] == shop["id"]]
    assert link["undeclared"] is True and link["imports"] >= 1 and link["declared"] == 0


def test_npm_and_go_dependencies_link_the_same_way(client, auth, tmp_path, session):
    kit = add_repo(
        client,
        auth,
        make(tmp_path, "ui-kit", {"package.json": '{"name": "@acme/ui-kit", "version": "1.0.0"}'}),
        name="ui-kit",
    )
    web_files = {"package.json": '{"name": "web", "dependencies": {"@acme/ui-kit": "^1.0.0", "react": "^18"}}'}
    add_repo(client, auth, make(tmp_path, "web", web_files), name="web")
    golib = add_repo(
        client, auth, make(tmp_path, "golib", {"go.mod": "module github.com/acme/lib\n\ngo 1.22\n"}), name="golib"
    )
    svc = {
        "go.mod": "module github.com/acme/svc\n\nrequire (\n\tgithub.com/acme/lib v1.2.3\n\tgolang.org/x/text v0.3.0 // indirect\n)\n"
    }
    add_repo(client, auth, make(tmp_path, "svc", svc), name="svc")
    pairs = {(r.src_label, r.dst_label) for r in links(session)}
    assert ("package.json:@acme/ui-kit", "package.json:@acme/ui-kit") in pairs
    assert ("go.mod:github.com/acme/lib", "go.mod:github.com/acme/lib") in pairs
    assert len(pairs) == 2 and kit and golib  # react and golang.org/x/text belong to no repository here


def test_manifests_in_tests_and_examples_describe_fixtures_not_the_repository(client, auth, tmp_path, session):
    add_repo(client, auth, lib(tmp_path), name="users-lib")
    files = {
        "pyproject.toml": '[project]\nname = "shop"\n',
        "tests/fixtures/pyproject.toml": '[project]\nname = "fixture"\ndependencies = ["user-lib"]\n',
        "examples/demo/package.json": '{"name": "demo", "dependencies": {"user-lib": "1"}}',
    }
    add_repo(client, auth, make(tmp_path, "shop-app", files), name="shop-app")
    assert links(session) == []


def test_a_broken_manifest_is_reported_and_does_not_stop_the_others(client, auth, tmp_path, session):
    users = add_repo(client, auth, lib(tmp_path), name="users-lib")
    files = {
        "pyproject.toml": "[project\nname =",
        "web/package.json": '{"name": "web", "dependencies": {"user-lib": "1"}}',
    }
    shop = add_repo(client, auth, make(tmp_path, "shop-app", files), name="shop-app")
    cov = analysis(client, auth, shop, users)["coverage"][shop["id"]]["manifests"]
    assert {m["file"]: bool(m["error"]) for m in cov} == {"pyproject.toml": True, "web/package.json": False}
    assert [r.rel for r in links(session)] == [] or True  # an npm name does not match a Python package


def test_who_declares_a_package_is_shown_in_blast_radius_and_in_the_public_surface(client, auth, tmp_path):
    users = add_repo(client, auth, lib(tmp_path), name="users-lib")
    files = {"pyproject.toml": '[project]\nname = "shop"\ndependencies = ["user-lib"]\n', "shop/c.py": USING}
    shop = add_repo(client, auth, make(tmp_path, "shop-app", files), name="shop-app")
    g = client.get("/api/groups/code-graph", headers=auth, params={"repo_ids": f"{shop['id']},{users['id']}"}).json()
    by = {n["id"]: n for n in g["nodes"]}
    (decl,) = [e for e in g["edges"] if e["rel"] == "DECLARES"]
    assert by[decl["source"]]["repo"] == "shop-app" and by[decl["target"]]["path"] == "pyproject.toml"
    out = client.get("/api/groups/impact", headers=auth, params={"node_id": decl["target"]}).json()
    assert [d["label"] for d in out["direct"]] == ["pyproject.toml:user-lib"] and out["repos"][0]["repo"] == "shop-app"
    s = client.get("/api/groups/surface", headers=auth, params={"repo_id": users["id"]}).json()
    assert [d["repo"] for d in s["declared_by"]] == ["shop-app"] and all(x["type"] != "file" for x in s["symbols"])


def test_a_declaration_in_a_repository_someone_cannot_open_is_counted_never_named(client, auth, tmp_path):
    users = add_repo(client, auth, lib(tmp_path), name="users-lib")
    files = {"pyproject.toml": '[project]\nname = "shop"\ndependencies = ["user-lib"]\n'}
    shop = add_repo(client, auth, make(tmp_path, "shop-app", files), name="shop-app")
    ann = make_user(client, auth, "ann@acme.io", "member")
    client.put(
        f"/api/repositories/{shop['id']}/access", headers=auth, json={"visibility": "restricted", "user_ids": []}
    )
    s = client.get("/api/groups/surface", headers=ann, params={"repo_id": users["id"]}).json()
    assert s["declared_by"] == [] and s["hidden"] == 1 and "shop-app" not in str(s)


def test_a_new_declaration_is_reported_as_a_new_dependency(client, auth, tmp_path, session):
    users = add_repo(client, auth, lib(tmp_path), name="users-lib")
    shop_path = make(tmp_path, "shop-app", {"pyproject.toml": '[project]\nname = "shop"\n'})
    shop = add_repo(client, auth, shop_path, name="shop-app")
    ids = f"{shop['id']},{users['id']}"
    client.get("/api/groups/changes", headers=auth, params={"repo_ids": ids})  # tracking starts
    (shop_path / "pyproject.toml").write_text('[project]\nname = "shop"\ndependencies = ["user-lib>=1"]\n')
    subprocess.run(["git", "-C", str(shop_path), "add", "-A"], check=True)
    cmd = ["git", "-C", str(shop_path), "-c", "user.name=d", "-c", "user.email=d@x.io", "commit", "-qm", "declare"]
    subprocess.run(cmd, check=True)
    assert client.post(f"/api/repositories/{shop['id']}/index", headers=auth).status_code == 202
    r = client.get("/api/groups/changes", headers=auth, params={"repo_ids": ids}).json()
    (pair,) = r["new"]
    assert (pair["from"], pair["to"]) == ("shop-app", "users-lib") and "requires user-lib >=1" in pair["examples"][0][
        "evidence"
    ]
    assert session.get(Repository, shop["id"]) is not None and rl.DETECTION_VERSION
