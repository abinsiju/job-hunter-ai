import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api import auth as auth_mod
from app.api.deps import require_user
from app.core.config import settings
from app.core.security import hash_password
from app.main import app

PUBLIC = {"/health", "/auth/login"}
PW = "correct-horse-battery"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "x" * 48)
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", hash_password(PW))
    auth_mod._fails.clear()


client = TestClient(app)


def login(pw=PW, user="admin"):
    return client.post("/auth/login", json={"username": user, "password": pw})


def test_password_is_hashed_not_plaintext():
    assert settings.admin_password_hash.startswith("$argon2") and PW not in settings.admin_password_hash


def all_operations():
    """Every (method, path) the app serves, taken from its OpenAPI schema (robust to how routers are nested)."""
    import re
    ops = []
    for path, item in app.openapi()["paths"].items():
        for method in item:
            if method in ("get", "post", "put", "patch", "delete"):
                ops.append((method, path, re.sub(r"\{[^}]+\}", "1", path)))
    return ops


def test_every_route_except_public_requires_auth():
    ops = all_operations()
    paths = {p for _, p, _ in ops}
    assert len(ops) >= 24, f"only {len(ops)} operations found: the audit would be vacuous"  # guards against silently checking nothing
    for expected in ("/me", "/v1/overview", "/v1/listings", "/v1/listings/{listing_id}", "/v1/applications/{app_id}/status", "/v1/jobs",
                     "/v1/profile", "/v1/blocklist", "/v1/system", "/v1/assistant", "/v1/applications/{app_id}/documents/{name}"):
        assert expected in paths, f"{expected} missing from the schema"
    unprotected = []
    for method, path, url in ops:
        if path in PUBLIC:
            continue
        r = client.request(method.upper(), url, json={} if method != "get" else None)
        if r.status_code != 401:
            unprotected.append((method.upper(), path, r.status_code))
    assert not unprotected, f"routes reachable without a token: {unprotected}"


def test_docs_and_openapi_disabled():
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(p).status_code == 404


def test_health_public_me_protected():
    assert client.get("/health").status_code == 200
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_login_and_access():
    r = login()
    assert r.status_code == 200
    tok = r.json()["access_token"]
    assert client.get("/me", headers={"Authorization": f"Bearer {tok}"}).json() == {"user": "admin"}


def test_wrong_password_and_unknown_user_rejected():
    assert login("wrong-password-xx").status_code == 401
    assert login(user="nobody").status_code == 401


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "access_token_minutes", -1)
    tok = login().json()["access_token"]
    assert client.get("/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_token_signed_with_other_secret_rejected(monkeypatch):
    tok = login().json()["access_token"]
    monkeypatch.setattr(settings, "jwt_secret", "y" * 48)
    assert client.get("/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_lockout_after_repeated_failures():
    for _ in range(5):
        assert login("bad-password-xx").status_code == 401
    assert login().status_code == 429


def test_unconfigured_auth_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "")
    assert login().status_code == 503
    assert client.get("/me", headers={"Authorization": "Bearer abc"}).status_code == 401
