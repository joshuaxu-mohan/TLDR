"""Bearer auth tests for src.delivery.api (deny by default under /api).

Uses a throwaway SQLite file and makes no network calls: unauthenticated
requests stop at the middleware, and authenticated probes use missing ids or
invalid bodies that fail before any Gemini, Groq, Taddy or feed fetch.
Run from the repo root: python -m pytest tests/test_api.py -v
"""

import importlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-not-a-real-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
EXEMPT_PATHS = {"/api/whatsapp/webhook"}
MISSING_ID = "999999"


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("API_BEARER_TOKEN", TOKEN)
    mp.setenv("DB_PATH", str(tmp_path_factory.mktemp("db") / "test.db"))
    mp.setenv("TWILIO_AUTH_TOKEN", "")
    from src.config.settings import get_settings

    get_settings.cache_clear()
    sys.modules.pop("src.delivery.api", None)
    module = importlib.import_module("src.delivery.api")
    yield module
    mp.undo()
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def client(api):
    return TestClient(api.app, raise_server_exceptions=False)


def _write_routes(app):
    """Every non-GET/HEAD/OPTIONS operation under /api, with ids filled in.

    Read from the OpenAPI schema (what /openapi.json serves) rather than
    app.routes, whose shape for included routers varies across FastAPI versions.
    """
    http_methods = {"POST", "PUT", "PATCH", "DELETE", "TRACE"}
    out = []
    for template, operations in app.openapi()["paths"].items():
        if template.startswith("/api/"):
            for method in sorted(m.upper() for m in operations):
                if method in http_methods:
                    out.append((method, re.sub(r"\{[^}]+\}", MISSING_ID, template)))
    return out


def test_every_write_route_rejects_missing_token(api, client):
    routes = [r for r in _write_routes(api.app) if r[1] not in EXEMPT_PATHS]
    for expected in [
        ("PATCH", f"/api/sources/{MISSING_ID}"),
        ("DELETE", f"/api/sources/{MISSING_ID}"),
        ("POST", "/api/sources"),
        ("POST", "/api/sources/discover"),
        ("POST", "/api/sources/validate-url"),
    ]:
        assert expected in routes
    for method, path in routes:
        assert client.request(method, path).status_code == 401, (method, path)


def test_wrong_token_or_scheme_is_rejected(client):
    path = f"/api/sources/{MISSING_ID}"
    for header in [f"Bearer {TOKEN}x", TOKEN, f"Basic {TOKEN}", "Bearer "]:
        resp = client.patch(path, json={}, headers={"Authorization": header})
        assert resp.status_code == 401, header


def test_path_variants_cannot_bypass(client):
    for method, path in [
        ("PATCH", f"/api/sources/{MISSING_ID}/"),
        ("DELETE", "/api/sources/%39%39%39"),
        ("DELETE", f"/api//sources/{MISSING_ID}"),
        ("DELETE", f"/%61pi/sources/{MISSING_ID}"),
        ("PATCH", f"/api/sources%2F{MISSING_ID}"),
        ("POST", "/api/sources/validate-url/"),
    ]:
        assert client.request(method, path).status_code == 401, (method, path)


@pytest.mark.parametrize(
    "method, path, body, expected",
    [
        ("PATCH", f"/api/sources/{MISSING_ID}", {}, 404),
        ("DELETE", f"/api/sources/{MISSING_ID}", None, 404),
        ("POST", "/api/sources", {}, 422),
        ("POST", "/api/sources/validate-url", {"url": ""}, 422),
        ("POST", "/api/sources/discover", {"type": "nope", "query": "x"}, 422),
        ("POST", f"/api/articles/{MISSING_ID}/extend", None, 404),
        ("POST", f"/api/articles/{MISSING_ID}/transcribe", None, 404),
        ("POST", "/api/summarise-page", {"article_ids": [int(MISSING_ID)]}, 400),
    ],
)
def test_valid_token_reaches_handler(client, method, path, body, expected):
    # /api/feed-summary/refresh is left out on purpose: with a valid token it
    # calls Gemini. Its 401 without a token is covered above.
    resp = client.request(method, path, json=body, headers=AUTH)
    assert resp.status_code == expected, resp.text


def test_reads_and_preflight_stay_open(client):
    assert client.get("/api/sources").status_code == 200
    assert client.get("/api/digests").status_code == 200
    preflight = client.options(
        "/api/sources",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "PATCH"},
    )
    assert preflight.status_code != 401


def test_whatsapp_webhook_is_exempt(client):
    # Twilio signs with X-Twilio-Signature, not a bearer token. Expect 403
    # from the handler's own signature check; anything but 401 proves the
    # middleware let it through.
    resp = client.post("/api/whatsapp/webhook", data={"From": "x", "Body": "y"})
    assert resp.status_code != 401


def test_missing_server_token_fails_closed(api, client, monkeypatch):
    monkeypatch.setattr(api, "_API_BEARER_TOKEN", "")
    assert client.patch(f"/api/sources/{MISSING_ID}", json={}, headers=AUTH).status_code == 503
    assert client.post("/api/sources/validate-url", json={"url": ""}).status_code == 503
    assert client.get("/api/sources").status_code == 200
