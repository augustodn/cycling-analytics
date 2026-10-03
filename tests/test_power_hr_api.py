"""Local and authenticated HTTP contracts for Power-HR analytics."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from cycling.api import create_app
from cycling.cloud_api import app
from cycling.postgres_store import PostgresStore
from cycling.storage import Store
from tests.test_cloud_api import _token
from tests.test_power_hr_service import FAST, write_ride


def test_local_power_hr_activity_period_comparison_and_validation(tmp_path):
    data_dir = tmp_path / "data"
    with Store(data_dir) as store:
        write_ride(store, "ride")
    with TestClient(create_app(data_dir)) as client:
        for body in (
            {"activity_id": "ride", **FAST},
            {"period": "all", "environment": "outdoor", **FAST},
            {"period": "all", "compare_period": {"period": "7d"}, **FAST},
        ):
            response = client.post("/power-hr", json=body)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["operation"] == "power_hr"
            assert result["parameter_mode"] == "historical"
            assert (
                result["data"]["current_period"]["environments"]["outdoor"][
                    "activity_count"
                ]
                == 1
            )
        assert (
            client.post("/power-hr", json={"activity_id": "missing"}).status_code == 404
        )
        assert client.post("/power-hr", json={"lag_s": 14}).status_code == 422
        assert (
            client.post(
                "/power-hr", json={"activity_id": "ride", "period": "30d"}
            ).status_code
            == 422
        )
        assert "/power-hr" in client.get("/openapi.json").json()["paths"]


def test_cloud_power_hr_requires_authentication_and_uses_request_owner(monkeypatch):
    secret = "x" * 32
    monkeypatch.setenv("INTERNAL_API_SECRET", secret)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    store = Mock(spec=PostgresStore)
    store.activity.side_effect = KeyError("Activity not found")
    store.activities.return_value = []
    constructor = Mock(return_value=store)
    monkeypatch.setattr("cycling.cloud_api.PostgresStore", constructor)
    headers = {"Authorization": f"Bearer {_token(secret, 'alice')}"}
    with TestClient(app) as client:
        assert client.post("/api/v1/power-hr", json={}).status_code == 401
        constructor.assert_not_called()
        response = client.post("/api/v1/power-hr", json={}, headers=headers)
        assert response.status_code == 200
        assert (
            response.json()["data"]["current_period"]["environments"]["outdoor"][
                "activities"
            ]
            == {}
        )
        constructor.assert_called_once_with("postgresql://test", "alice")
        store.close.assert_called_once()
        foreign = client.post(
            "/api/v1/power-hr", json={"activity_id": "bob-ride"}, headers=headers
        )
        assert foreign.status_code == 404
        store.samples.assert_not_called()
        forged = client.post(
            "/api/v1/power-hr", json={"user_id": "bob"}, headers=headers
        )
        assert forged.status_code == 422


@pytest.mark.parametrize("mode", ["observed", "stable"])
def test_cloud_without_athlete_parameters_returns_serializable_results(
    monkeypatch, mode
):
    monkeypatch.setenv("INTERNAL_API_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    store = Mock(spec=PostgresStore)
    store.activity.return_value = {
        "id": "ride",
        "start_time": "2026-01-01T00:00:00Z",
        "duration_s": 600,
        "modality": "road",
    }
    store.parameters.return_value = (None, None)
    store.samples.return_value = [
        {
            "elapsed_s": second,
            "segment": 0,
            "active": True,
            "power_w": 200,
            "hr_bpm": 140,
            "cadence_rpm": 90,
        }
        for second in range(600)
    ]
    monkeypatch.setattr("cycling.cloud_api.PostgresStore", Mock(return_value=store))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/power-hr",
            json={"activity_id": "ride", "mode": mode, **FAST},
            headers={"Authorization": f"Bearer {_token('x' * 32)}"},
        )
    assert response.status_code == 200, response.text
    activity = response.json()["data"]["current_period"]["environments"]["outdoor"][
        "activities"
    ]["ride"]
    assert activity["effective_ftp_w"] is None
    assert activity["available"] is (mode == "observed")
    assert activity["reason"] == (None if mode == "observed" else "missing_ftp")
    store.samples.assert_called_once_with("ride")
    store.parameters.assert_called_once()
    assert store.parameters.call_args.kwargs == {"mode": "historical"}
    store.save_metrics.assert_not_called()
