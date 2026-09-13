import json
from pathlib import Path

import duckdb

from cycling.ingestion import ingest
from cycling.storage import Store


def test_legacy_catalog_schema_migration_and_operations(tmp_path: Path):
    root = tmp_path / ".cycling"
    root.mkdir(parents=True, exist_ok=True)
    (root / "samples").mkdir(parents=True, exist_ok=True)

    db_path = root / "catalog.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE sources (
            path VARCHAR PRIMARY KEY, hash VARCHAR, size BIGINT,
            status VARCHAR, error VARCHAR, last_seen VARCHAR);
        CREATE TABLE activities (
            id VARCHAR PRIMARY KEY, metadata VARCHAR, sample_path VARCHAR,
            normalizer_version VARCHAR, ingested_at VARCHAR);
        CREATE TABLE parameters (
            id VARCHAR PRIMARY KEY, effective_date DATE, recorded_at VARCHAR,
            settings VARCHAR);
        CREATE TABLE metrics (
            id VARCHAR PRIMARY KEY, activity_id VARCHAR, cache_key VARCHAR,
            computed_at VARCHAR, result VARCHAR);
        CREATE TABLE intervals (
            id VARCHAR PRIMARY KEY, activity_id VARCHAR, interval_type VARCHAR,
            start_s INT, end_s INT, metrics VARCHAR);
        CREATE TABLE power_curve (
            activity_id VARCHAR, duration_s INT, max_power_w DOUBLE,
            PRIMARY KEY (activity_id, duration_s));
        CREATE TABLE daily_training_load (
            date DATE PRIMARY KEY, ctl DOUBLE, atl DOUBLE, tsb DOUBLE,
            total_load DOUBLE);
        CREATE TABLE processing_runs (
            id VARCHAR PRIMARY KEY, run_at VARCHAR, status VARCHAR, summary VARCHAR);
        CREATE TABLE quality_flags (
            activity_id VARCHAR, flag VARCHAR, PRIMARY KEY (activity_id, flag));
        CREATE TABLE activity_context (
            id VARCHAR PRIMARY KEY, activity_id VARCHAR,
            recorded_at VARCHAR, context VARCHAR);
        CREATE TABLE acquisition_audits (
            root VARCHAR PRIMARY KEY, audited_at VARCHAR, summary VARCHAR);
    """)

    legacy_meta = {
        "source_hash": "legacy_hash_1",
        "strava_id_hint": "12345678",
        "start_time": "2026-01-01T10:00:00Z",
        "elapsed_seconds": 3600,
        "modality": "ride",
    }
    conn.execute(
        "INSERT INTO activities VALUES (?, ?, ?, ?, ?)",
        [
            "legacy_act_1",
            json.dumps(legacy_meta),
            "samples/2026/01/legacy_act_1.parquet",
            "0.1.0",
            "2026-01-01T10:05:00Z",
        ],
    )
    conn.execute(
        "INSERT INTO activities VALUES (?, ?, ?, ?, ?)",
        [
            "legacy_act_null_meta",
            None,
            "samples/2026/01/legacy_act_null.parquet",
            "0.1.0",
            "2026-01-01T10:06:00Z",
        ],
    )
    conn.close()

    # Open Store with legacy catalog
    store = Store(root=root)

    # Verify activities query and store methods do not crash
    acts = store.activities()
    assert len(acts) == 2

    act1 = store.activity("legacy_act_1")
    assert act1["id"] == "legacy_act_1"
    assert act1["source_hash"] == "legacy_hash_1"
    assert act1["strava_id_hint"] == "12345678"
    assert act1["start_time"] == "2026-01-01T10:00:00Z"
    assert act1["duration_s"] == 3600
    assert act1["modality"] == "ride"

    act_null = store.activity("legacy_act_null_meta")
    assert act_null["id"] == "legacy_act_null_meta"
    assert act_null["source_hash"] == "legacy_act_null_meta"
    assert act_null["start_time"] == ""
    assert act_null["duration_s"] == 0
    assert act_null["modality"] == "unknown"

    status = store.status()
    assert status["activities"] == 2
    assert "legacy_act_1" in status["missing_samples"]

    # Verify ingestion with legacy store
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    fit_file = downloads_dir / "activity_12345678.fit"

    # Minimal valid FIT header / bytes or use fit parser fixture if needed
    # Create a dummy file and test ingest handles source errors cleanly or processes valid source
    fit_file.write_bytes(b"INVALID_FIT_HEADER")
    res = ingest(store, source=downloads_dir)
    assert res["discovered"] == 1
    assert len(res["errors"]) == 1

    store.close()
