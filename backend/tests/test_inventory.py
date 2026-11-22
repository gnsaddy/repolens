import subprocess

from repolens.services import inventory
from tests.test_api import add_repo
from tests.test_chat import new_chat, say


def names(kind, path, text):
    return [i.name for i in inventory.extract(kind, path, text)]


def test_environment_variables_are_read_from_code_settings_and_example_files():
    py = 'import os\nA = os.environ["A_KEY"]\nB = os.environ.get("B_KEY", "x")\nC = os.getenv("C_KEY")\n'
    assert names(inventory.ENV, "app.py", py) == ["A_KEY", "B_KEY", "C_KEY"]
    js = "const a = process.env.API_URL; const b = process.env['TOKEN']; const c = import.meta.env.VITE_BASE;"
    assert names(inventory.ENV, "web.ts", js) == ["API_URL", "TOKEN", "VITE_BASE"]
    assert names(inventory.ENV, "main.go", 'x := os.Getenv("PORT")') == ["PORT"]
    settings = (
        "class Settings(BaseSettings):\n"
        '    model_config = SettingsConfigDict(env_prefix="APP_")\n'
        "    database_url: str = 'sqlite://'\n"
        "    debug: bool = False\n"
        "    def other(self): ...\n"
    )
    got = {i.name: i.detail for i in inventory.extract(inventory.ENV, "config.py", settings)}
    assert set(got) == {"APP_DATABASE_URL", "APP_DEBUG"} and "default False" in got["APP_DEBUG"]
    example = "# Where the API listens\nPORT=8000\nexport SECRET_KEY=change-me\n"
    found = {i.name: i.detail for i in inventory.extract(inventory.ENV, ".env.example", example)}
    assert found == {"PORT": "Where the API listens", "SECRET_KEY": "listed in the example file"}
    assert inventory.extract(inventory.ENV, ".env", example) == []  # a real env file is not an example


def test_commands_come_from_argparse_click_node_go_make_and_package_json():
    argparse = 'sub.add_parser("serve")\np.add_argument("--port")\np.add_argument("name")\n'
    assert names(inventory.CLI, "cli.py", argparse) == ["serve", "--port"]
    click = (
        '@cli.command()\ndef run_all():\n    pass\n\n@cli.command("sync")\n@click.option("--dry-run")\ndef s(): ...\n'
    )
    assert names(inventory.CLI, "cli.py", click) == ["run-all", "sync", "--dry-run"]
    assert names(inventory.CLI, "bin.js", "program.command('build').option('-o, --out <dir>')") == [
        "build",
        "-o, --out <dir>",
    ]
    assert names(inventory.CLI, "cmd.go", 'cmd := &cobra.Command{Use: "deploy"}\nflag.String("env", "", "")') == [
        "deploy",
        "--env",
    ]
    make = ".PHONY: check\ncheck: ## lint and test\n\t@echo\nbuild:\n\t@echo\n%.o: %.c\n"
    got = {i.name: i.detail for i in inventory.extract(inventory.CLI, "Makefile", make)}
    assert got == {"make check": "lint and test", "make build": "make target"}
    pkg = '{"name": "x", "scripts": {"dev": "vite", "test": "vitest run"}}'
    assert names(inventory.CLI, "package.json", pkg) == ["npm run dev", "npm run test"]


def test_tables_come_from_models_and_sql_and_migrations_are_left_out():
    sqla = (
        "class User(Base):\n"
        '    __tablename__ = "users"\n'
        "    id: Mapped[str] = mapped_column(primary_key=True)\n"
        "    email = Column(String)\n"
        "    def f(self): ...\n"
        "class Helper:\n    pass\n"
    )
    got = {i.name: i.detail for i in inventory.extract(inventory.TABLES, "models.py", sqla)}
    assert got == {"users": "columns id, email"}
    assert inventory.extract(inventory.TABLES, "alembic/versions/0001_x.py", sqla) == []
    django = "class Order(models.Model):\n    total = models.IntegerField()\n    owner = models.ForeignKey('User')\n"
    assert [(i.name, i.detail) for i in inventory.extract(inventory.TABLES, "m.py", django)] == [
        ("order", "columns total, owner")
    ]
    sql = "CREATE TABLE IF NOT EXISTS orders (\n  id int,\n  total int,\n  PRIMARY KEY (id)\n);\n"
    assert [(i.name, i.detail) for i in inventory.extract(inventory.TABLES, "db/schema.sql", sql)] == [
        ("orders", "columns id, total")
    ]
    rails = 'create_table "posts" do |t|\n  t.string "title"\nend\n'
    assert names(inventory.TABLES, "db/schema.rb", rails) == ["posts"]


def test_tests_and_docs_are_ignored_and_chunks_read_back_unchanged():
    assert inventory.extract(inventory.ENV, "tests/test_x.py", 'os.getenv("A")') == []
    assert inventory.extract(inventory.ENV, "README.md", 'os.getenv("A")') == []
    assert inventory.chunks("x.py", "print(1)") == []
    (c,) = inventory.chunks("Makefile", "check: ## lint and test\n")
    assert c.chunk_type == inventory.CLI and inventory.parse(c.content) == inventory.extract(
        inventory.CLI, "Makefile", "check: ## lint and test\n"
    )


def test_each_question_picks_its_own_inventory():
    from repolens.services.chat import wanted_inventory as want

    assert want("List all the database tables") == inventory.TABLES
    assert want("What environment variables does this service read?") == inventory.ENV
    assert want("show all cli commands") == inventory.CLI
    assert want("How does the login flow work?") is None


def test_listing_all_of_them_is_exact_and_cited(client, auth, tmp_path):
    d = tmp_path / "svc"
    d.mkdir()
    (d / "config.py").write_text('import os\nURL = os.environ["DATABASE_URL"]\nKEY = os.getenv("API_KEY")\n')
    (d / "models.py").write_text(
        'class A(Base):\n    __tablename__ = "accounts"\n    id: Mapped[int] = mapped_column()\n'
    )
    (d / "Makefile").write_text("check: ## lint and test\n\t@echo\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(d), *cmd], check=True)
    repo = add_repo(client, auth, str(d), name="svc")
    c = new_chat(client, auth, repo_ids=[repo["id"]])
    for question, wants in (
        ("List all the environment variables", ("DATABASE_URL", "API_KEY", "**2** environment variables")),
        ("Which database tables are there?", ("accounts", "columns id")),
        ("List all the commands", ("make check", "lint and test")),
    ):
        r = say(client, auth, c["id"], question)
        assert r.status_code == 201
        text = r.json()["items"][-1]["content"]
        for w in wants:
            assert w in text, (question, text)
