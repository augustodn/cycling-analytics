import json
from time import time
from unittest.mock import Mock

import jwt
import pytest
from fastapi.testclient import TestClient

from cycling.cloud_api import app


def _token(secret: str, subject: str = "google-sub"):
    now = int(time())
    return jwt.encode(
        {
            "sub": subject,
            "iss": "cycling-nextjs",
            "aud": "cycling-fastapi",
            "iat": now,
            "exp": now + 60,
        },
        secret,
        algorithm="HS256",
    )


def test_health_is_public_liveness_only(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_tenant_routes_require_signed_internal_identity(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "x" * 32)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        missing = client.get("/api/v1/activities")
        invalid = client.get(
            "/api/v1/activities", headers={"Authorization": "Bearer forged"}
        )
        valid = client.get(
            "/api/v1/activities",
            headers={"Authorization": f"Bearer {_token('x' * 32)}"},
        )
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 503


def test_token_issuer_audience_and_expiration_are_enforced(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "x" * 32)
    with TestClient(app) as client:
        wrong_audience = jwt.encode(
            {
                "sub": "google-sub",
                "iss": "cycling-nextjs",
                "aud": "other",
                "iat": int(time()),
                "exp": int(time()) + 60,
            },
            "x" * 32,
            algorithm="HS256",
        )
        response = client.get(
            "/api/v1/activities",
            headers={"Authorization": f"Bearer {wrong_audience}"},
        )
    assert response.status_code == 401


def test_request_timing_includes_store_init_without_private_data(monkeypatch, capsys):
    secret = "x" * 32
    token = _token(secret, "private-user@example.test")
    monkeypatch.setenv("INTERNAL_API_SECRET", secret)
    monkeypatch.setenv("DATABASE_URL", "postgresql://private-database")
    store = Mock()
    store.activities.return_value = []
    constructor = Mock(return_value=store)
    monkeypatch.setattr("cycling.cloud_api.PostgresStore", constructor)
    monkeypatch.setattr(
        "cycling.cloud_api.perf_counter",
        Mock(side_effect=[10.0, 10.1, 10.15, 10.3]),
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/activities?email=private-query@example.test",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["data"] == {"activities": []}
    assert response.headers["server-timing"] == "app;dur=300.00, store_init;dur=50.00"
    constructor.assert_called_once_with(
        "postgresql://private-database", "private-user@example.test"
    )
    store.close.assert_called_once()
    event = json.loads(capsys.readouterr().out)
    assert event == {
        "event": "request_timing",
        "method": "GET",
        "path": "/api/v1/activities",
        "status": 200,
        "elapsed_ms": 300.0,
        "store_init_ms": 50.0,
    }


def test_request_timing_covers_rejections_and_redacts_unmatched_paths(
    monkeypatch, capsys
):
    monkeypatch.setenv("INTERNAL_API_SECRET", "x" * 32)
    with TestClient(app) as client:
        unauthorized = client.get("/api/v1/activities")
        missing = client.get("/private-user@example.test?jwt=private-token")
    assert unauthorized.status_code == 401
    assert missing.status_code == 404
    assert "server-timing" in unauthorized.headers
    assert "store_init" not in unauthorized.headers["server-timing"]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(e["path"], e["status"]) for e in events] == [
        ("/api/v1/activities", 401),
        ("<unmatched>", 404),
    ]
    assert all(e["elapsed_ms"] >= 0 and "store_init_ms" not in e for e in events)


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(PermissionError("private-user"), 401), (RuntimeError("private-token"), 500)],
)
def test_request_timing_covers_failed_store_initialization(
    monkeypatch, capsys, error, status_code
):
    monkeypatch.setenv("INTERNAL_API_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr("cycling.cloud_api.PostgresStore", Mock(side_effect=error))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/v1/activities",
            headers={"Authorization": f"Bearer {_token('x' * 32)}"},
        )
    assert response.status_code == status_code
    event = json.loads(capsys.readouterr().out)
    assert set(event) == {
        "event",
        "method",
        "path",
        "status",
        "elapsed_ms",
        "store_init_ms",
    }
    assert event["status"] == status_code
    assert 0 <= event["store_init_ms"] <= event["elapsed_ms"]
    if status_code == 401:
        assert "store_init;dur=" in response.headers["server-timing"]
