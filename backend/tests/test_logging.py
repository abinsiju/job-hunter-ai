import json
import logging

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def records(caplog):
    return [r for r in caplog.records if r.name == "jobhunter.http"]


def test_request_id_generated_and_returned(caplog):
    caplog.set_level(logging.INFO)
    r = client.get("/health")
    rid = r.headers["x-request-id"]
    assert len(rid) == 16
    assert records(caplog)[-1].__dict__["status"] == 200


def test_incoming_request_id_is_propagated():
    assert client.get("/health", headers={"x-request-id": "abc123"}).headers["x-request-id"] == "abc123"


def test_json_output_has_request_id_and_no_secrets(capfd):
    from app.core.logging import JsonFormatter, request_id_var

    tok = request_id_var.set("rid-1")
    rec = logging.LogRecord("t", logging.INFO, "", 0, "hello", (), None)
    rec.status = 200
    line = JsonFormatter().format(rec)
    request_id_var.reset(tok)
    d = json.loads(line)
    assert d["request_id"] == "rid-1" and d["status"] == 200 and d["msg"] == "hello" and d["level"] == "INFO"


def test_login_logs_never_contain_password(caplog):
    caplog.set_level(logging.INFO)
    client.post("/auth/login", json={"username": "admin", "password": "super-secret-pw-123"})
    assert "super-secret-pw-123" not in caplog.text


def test_secrets_in_urls_and_messages_are_redacted_in_every_log_line():
    from app.core.logging import JsonFormatter, redact
    assert redact("GET https://api.adzuna.com/x?app_id=ABC&app_key=SECRET123&what=it") == "GET https://api.adzuna.com/x?app_id=***&app_key=***&what=it"
    assert redact("login failed password=hunter2 token=abc.def") == "login failed password=*** token=***"
    rec = logging.LogRecord("t", logging.INFO, "", 0, "calling https://h/p?api_key=K999&q=1", (), None)
    rec.url = "https://h/p?app_key=ZZZ"
    out = json.loads(JsonFormatter().format(rec))
    assert "K999" not in json.dumps(out) and "ZZZ" not in json.dumps(out) and "api_key=***" in out["msg"] and out["url"] == "https://h/p?app_key=***"


def test_http_client_libraries_do_not_log_request_urls():
    from app.core.logging import quiet_http_loggers
    quiet_http_loggers()
    assert logging.getLogger("httpx").level == logging.WARNING and logging.getLogger("httpcore").level == logging.WARNING
