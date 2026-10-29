"""Imports in Go, Java, Kotlin, C#, Rust, C and C++ link a file to what another repository defines."""

import pytest

from repolens.models import RepoLink
from repolens.services import lang_imports as li
from repolens.services import repo_links as rl
from tests.test_api import add_repo
from tests.test_groups import days_ago
from tests.test_repo_links import ids
from tests.test_risk import commit, git

# --- reading the lines ----------------------------------------------------------------------------------------------


def test_go_imports_in_blocks_aliases_and_single_lines():
    text = 'package main\n\nimport "fmt"\nimport pay "github.com/acme/pay/v2"\n\nimport (\n\t"os"\n\t_ "x.io/driver"\n\tbilling "github.com/acme/billing/client" // the client\n)\n'
    got = {(spec, alias) for spec, alias, _ in li.parse_go(text)}
    assert got == {
        ("fmt", ""),
        ("github.com/acme/pay/v2", "pay"),
        ("os", ""),
        ("x.io/driver", "_"),
        ("github.com/acme/billing/client", "billing"),
    }


def test_java_and_kotlin_imports_with_static_and_wildcards():
    text = "package a.b;\nimport com.acme.user.UserService;\nimport static com.acme.util.Strings.trim;\nimport com.acme.model.*;\nimport com.acme.x.Y as Z\n"
    assert li.parse_jvm(text) == [
        ("com.acme.user.UserService", False, False),
        ("com.acme.util.Strings.trim", True, False),
        ("com.acme.model", False, True),
        ("com.acme.x.Y", False, False),
    ]


def test_c_includes_keep_the_path_and_the_bracket():
    text = '#include <stdio.h>\n#  include "billing/api.h"\n// #include "no.h"\n'
    assert li.parse_includes(text) == [("stdio.h", "<"), ("billing/api.h", '"')]


def test_csharp_usings_skip_blocks_and_take_aliases():
    text = "using System;\nusing Acme.Billing;\nusing static Acme.Util.Maths;\nusing Json = Newtonsoft.Json;\nusing (var x = y) { }\n"
    assert li.parse_using(text) == ["System", "Acme.Billing", "Acme.Util.Maths", "Newtonsoft.Json"]


def test_rust_use_trees_expand_to_paths():
    text = "use std::fmt;\nuse userlib::client::{Get, post as send, self};\npub use userlib::{core::Run, util::*};\n"
    paths = [p for p, _ in li.parse_use(text)]
    assert paths == [
        "std::fmt",
        "userlib::client::Get",
        "userlib::client::post",
        "userlib::client",
        "userlib::core::Run",
        "userlib::util::*",
    ]


# --- finding the dependency between two indexed repositories -----------------------------------------------------------


def two(client, auth, tmp_path, provider, consumer):
    """Index a repository that offers code and one that uses it; return both."""
    repos = []
    for name, files in (("provider", provider), ("consumer", consumer)):
        p = tmp_path / name
        p.mkdir()
        git(p, "init", "-q", "-b", "main")
        commit(p, files, f"feat: {name}", days_ago(3))
        repos.append(add_repo(client, auth, p, name=name))
    return repos


def links(client, auth, session, repos):
    client.get("/api/groups/analysis", headers=auth, params={"repo_ids": ids(*repos)})
    session.expire_all()
    return session.query(RepoLink).filter_by(kind="import", gone_at=None).all()


def test_a_go_import_links_the_file_to_the_function_it_calls(client, auth, tmp_path, session):
    provider = {
        "go.mod": "module github.com/acme/pay\n\ngo 1.22\n",
        "client/client.go": "package client\n\nfunc Charge(id string) int {\n\treturn 1\n}\n\nfunc Refund(id string) int {\n\treturn 2\n}\n",
        "client/client_test.go": 'package client\n\nfunc TestCharge() {\n\tCharge("a")\n}\n',
    }
    consumer = {
        "go.mod": "module github.com/acme/shop\n\ngo 1.22\n\nrequire github.com/acme/pay v1.0.0\n",
        "cmd/main.go": 'package main\n\nimport (\n\t"fmt"\n\t"github.com/acme/pay/client"\n)\n\nfunc main() {\n\tfmt.Println(client.Charge("a"))\n}\n',
    }
    repos = two(client, auth, tmp_path, provider, consumer)
    rows = [r for r in links(client, auth, session, repos) if r.src_label == "cmd/main.go"]
    assert [(r.dst_label, r.trust, r.rel) for r in rows] == [("client/client.go:Charge", "fact", "IMPORTS")]
    assert 'import "github.com/acme/pay/client"' in rows[0].evidence


def test_a_go_package_nobody_offers_makes_no_link(client, auth, tmp_path, session):
    provider = {"go.mod": "module github.com/acme/pay\n", "client/client.go": "package client\n\nfunc Charge() {}\n"}
    consumer = {
        "go.mod": "module github.com/acme/shop\n",
        "main.go": 'package main\n\nimport "github.com/other/thing/client"\n\nfunc main() {\n\tclient.Charge()\n}\n',
    }
    assert links(client, auth, session, two(client, auth, tmp_path, provider, consumer)) == []


def test_a_java_import_links_the_class_and_a_repository_with_its_own_copy_does_not(client, auth, tmp_path, session):
    provider = {
        "src/main/java/com/acme/user/UserService.java": "package com.acme.user;\n\npublic class UserService {\n    public String find(String id) {\n        return id;\n    }\n}\n",
    }
    consumer = {
        "src/main/java/com/acme/shop/Checkout.java": "package com.acme.shop;\n\nimport com.acme.user.UserService;\nimport java.util.List;\n\npublic class Checkout {\n    UserService users = new UserService();\n}\n",
        "src/main/java/com/acme/shop/Other.java": "package com.acme.shop;\n\nimport com.acme.user.Missing;\n\npublic class Other {\n}\n",
    }
    repos = two(client, auth, tmp_path, provider, consumer)
    rows = links(client, auth, session, repos)
    assert [(r.src_label.split("/")[-1], r.dst_label.split("/")[-1], r.trust) for r in rows] == [
        ("Checkout.java", "UserService.java:UserService", "fact")
    ]


def test_a_kotlin_file_imports_a_java_class_of_another_repository(client, auth, tmp_path, session):
    provider = {"src/com/acme/user/UserService.java": "package com.acme.user;\n\npublic class UserService {\n}\n"}
    consumer = {
        "src/app/Main.kt": "package app\n\nimport com.acme.user.UserService\n\nfun main() {\n    println(UserService())\n}\n"
    }
    rows = links(client, auth, session, two(client, auth, tmp_path, provider, consumer))
    assert len(rows) == 1 and rows[0].src_label == "src/app/Main.kt" and rows[0].dst_label.endswith("UserService")


def test_a_c_include_links_the_header_when_only_one_other_repository_has_it(client, auth, tmp_path, session):
    provider = {
        "include/billing/api.h": "int charge(int id);\n",
        "src/api.c": '#include "billing/api.h"\nint charge(int id) { return id; }\n',
    }
    consumer = {
        "src/main.c": '#include <stdio.h>\n#include "billing/api.h"\n#include "local.h"\nint main(void) { return charge(1); }\n',
        "src/local.h": "int local(void);\n",
    }
    rows = links(client, auth, session, two(client, auth, tmp_path, provider, consumer))
    assert [(r.src_label, r.dst_label, r.trust) for r in rows] == [("src/main.c", "include/billing/api.h", "fact")]


def test_a_header_both_repositories_have_makes_no_link(client, auth, tmp_path, session):
    both = {"include/util.h": "int util(void);\n"}
    provider = {**both, "a.c": '#include "util.h"\n'}
    consumer = {**both, "src/main.c": '#include "util.h"\nint main(void) { return util(); }\n'}
    assert links(client, auth, session, two(client, auth, tmp_path, provider, consumer)) == []


def test_a_csharp_using_links_the_type_the_file_names(client, auth, tmp_path, session):
    provider = {
        "Billing/Invoice.cs": "namespace Acme.Billing\n{\n    public class Invoice\n    {\n    }\n}\n",
        "Billing/Ledger.cs": "namespace Acme.Billing\n{\n    public class Ledger\n    {\n    }\n}\n",
    }
    consumer = {
        "Shop/Cart.cs": "using System;\nusing Acme.Billing;\n\nnamespace Acme.Shop\n{\n    public class Cart\n    {\n        Invoice last;\n    }\n}\n",
        "Shop/Plain.cs": "using System;\nusing Newtonsoft.Json;\n\nnamespace Acme.Shop\n{\n    public class Plain\n    {\n    }\n}\n",
    }
    rows = links(client, auth, session, two(client, auth, tmp_path, provider, consumer))
    assert [(r.src_label, r.dst_label.split(":")[0], r.trust) for r in rows] == [
        ("Shop/Cart.cs", "Billing/Invoice.cs", "fact")
    ]


def test_a_rust_use_follows_the_module_to_its_file(client, auth, tmp_path, session):
    provider = {
        "Cargo.toml": '[package]\nname = "user-lib"\nversion = "0.1.0"\n',
        "src/lib.rs": "pub mod client;\n",
        "src/client.rs": "pub fn get_user(id: u32) -> u32 {\n    id\n}\n",
    }
    consumer = {
        "Cargo.toml": '[package]\nname = "shop"\nversion = "0.1.0"\n\n[dependencies]\nuser-lib = "0.1"\n',
        "src/main.rs": "use user_lib::client::get_user;\nuse std::fmt;\n\nfn main() {\n    get_user(1);\n}\n",
    }
    rows = links(client, auth, session, two(client, auth, tmp_path, provider, consumer))
    assert len(rows) == 1 and rows[0].src_label == "src/main.rs" and rows[0].dst_label.startswith("src/client.rs")
    assert rows[0].trust == "fact" and "use user_lib::client::get_user;" in rows[0].evidence


def test_a_rust_crate_from_the_same_repository_makes_no_link(client, auth, tmp_path, session):
    provider = {"Cargo.toml": '[package]\nname = "user-lib"\n', "src/lib.rs": "pub fn get() {}\n"}
    consumer = {
        "Cargo.toml": '[package]\nname = "shop"\n',
        "src/main.rs": "use crate::util::help;\nuse serde::Serialize;\n\nfn main() {}\n",
        "src/util.rs": "pub fn help() {}\n",
    }
    assert links(client, auth, session, two(client, auth, tmp_path, provider, consumer)) == []


def test_the_new_languages_are_read_and_the_coverage_says_so(client, auth, tmp_path, session):
    provider = {"go.mod": "module github.com/acme/pay\n", "client/client.go": "package client\n\nfunc Charge() {}\n"}
    consumer = {
        "cmd/main.go": 'package main\n\nimport "github.com/acme/pay/client"\n\nfunc main() {\n\tclient.Charge()\n}\n',
        "tool.rb": "puts 1\n",
    }
    repos = two(client, auth, tmp_path, provider, consumer)
    a = client.get("/api/groups/analysis", headers=auth, params={"repo_ids": ids(*repos)}).json()
    cov = a["coverage"]
    assert "github.com/acme/pay" in cov[repos[0]["id"]]["provides"]
    unread = {(u["detector"], u["language"]) for u in cov[repos[1]["id"]]["unread"]}
    assert ("imports", "ruby") in unread and ("imports", "go") not in unread
    link = next(x for x in a["links"] if x["src"] == repos[1]["id"])
    assert link["imports"] >= 1


@pytest.mark.parametrize("path", ["vendor/x/y.go", "docs/example.go", "client/client_test.go"])
def test_examples_vendored_code_and_tests_are_left_out(path):
    assert rl._skip_path(path)


def test_the_detection_version_changed_so_old_links_are_worked_out_again():
    assert rl.DETECTION_VERSION >= "d5"
