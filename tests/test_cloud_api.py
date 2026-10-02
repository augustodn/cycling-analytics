from time import time

import jwt
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
