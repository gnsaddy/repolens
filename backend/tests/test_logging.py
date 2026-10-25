import json
import logging

from repolens import config, telemetry


def configure(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("REPOLENS_LOG_FILE", str(tmp_path / "app.log"))
    for k, v in env.items():
        monkeypatch.setenv(f"REPOLENS_{k.upper()}", str(v))
    config.get_settings.cache_clear()
    telemetry.setup_logging()
    return tmp_path / "app.log"


def flush():
    for h in logging.getLogger().handlers:
        h.flush()


def test_the_log_file_never_grows_past_its_limit(monkeypatch, tmp_path):
    path = configure(monkeypatch, tmp_path, log_max_mb=1, log_backups=2)
    log = logging.getLogger("repolens.test")
    for i in range(60_000):  # about 6 MB of lines
        log.info("line %d %s", i, "x" * 80)
    flush()
    files = sorted(tmp_path.glob("app.log*"))
    assert len(files) == 3 and all(
        f.stat().st_size <= 1024 * 1024 + 400 for f in files
    )  # current + two backups, 1 MB each
    assert sum(f.stat().st_size for f in files) < 3.1 * 1024 * 1024
    assert json.loads(path.read_text().splitlines()[0])["logger"] == "repolens.test"


def test_with_a_log_file_only_warnings_stay_on_standard_output(monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path)
    logging.getLogger("repolens.test").info("routine")
    logging.getLogger("repolens.test").warning("needs attention")
    flush()
    err = capsys.readouterr().err
    assert "needs attention" in err and "routine" not in err
    assert "routine" in (tmp_path / "app.log").read_text()


def test_access_logs_record_the_path_but_never_what_people_searched_for(client, auth, monkeypatch, tmp_path):
    path = configure(monkeypatch, tmp_path)
    client.get("/api/wiki/search?q=secret+salary+plan", headers=auth)
    client.get("/api/auth/me", headers=auth)
    flush()
    text = path.read_text()
    assert (
        "GET /api/wiki/search 200" in text
        and "salary" not in text
        and "?" not in "".join(line for line in text.splitlines() if "repolens.access" in line)
    )


def test_polled_endpoints_are_logged_only_when_they_fail(client, auth, monkeypatch, tmp_path):
    path = configure(monkeypatch, tmp_path)
    for _ in range(3):
        client.get("/healthz")
        client.get("/api/wiki/drift/count", headers=auth)
    client.get("/api/wiki/drift/count")  # not signed in: worth a line
    flush()
    text = path.read_text()
    assert "/healthz" not in text and text.count("/api/wiki/drift/count") == 1 and "401" in text
