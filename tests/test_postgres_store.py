"""Persistence checks; no changes to the legacy DuckDB test suite.

Unit checks: ``uv run python -m pytest tests/test_postgres_store.py``.
Integration checks require psycopg[binary]>=3.2,<4 and a disposable database:

    docker run --rm -d --name cycling-pg-test -p 55432:5432 \
        -e POSTGRES_PASSWORD=test -e POSTGRES_DB=cycling_test postgres:17
    export TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/cycling_test
    uv run python -m pytest tests/test_postgres_store.py
    docker stop cycling-pg-test

Wait for pg_isready before testing. Each test creates/drops its own schema;
never point TEST_DATABASE_URL at production. Integration tests skip without
TEST_DATABASE_URL or psycopg. Blob tests use a fake SDK, never real credentials.
Production requires vercel==0.11.4 and a PRIVATE Blob store/token; a live private
Blob round trip remains a deployment smoke test.
"""

import hashlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
from xml.etree.ElementTree import ParseError

import pytest

from cycling.ingestion import ingest
from cycling.migrations import apply_migrations
from cycling.models import (
    ActivityContext,
    ActivityRequest,
    AthleteParameters,
    LoadRequest,
    PeriodPowerCurveRequest,
)
from cycling.postgres_store import PostgresStore, _ObjectStore
from cycling.service import CyclingService
from cycling.storage import Store
from tests.test_parsers import tcx_text


@pytest.fixture(autouse=True)
def local_environment(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)


@pytest.mark.parametrize("user_id", [None, "", " ", " owner", 123, "x" * 257, "a\x00b"])
def test_authenticated_owner_required_before_connecting(user_id):
    with pytest.raises(ValueError):
        PostgresStore("not-a-database", user_id)


def test_local_immutable_objects_and_cross_owner_keys(tmp_path):
    alice = _ObjectStore("alice", tmp_path)
    bob = _ObjectStore("bob", tmp_path)
    key = alice.put(b"original", "originals", ".tcx")
    again = alice.put(b"original", "originals", ".tcx")
    assert key != again
    assert _ObjectStore("alice", tmp_path).read(key) == b"original"
    assert alice.exists(key)
    with pytest.raises(ValueError):
        bob.read(key)
    for invalid in [
        "https://attacker.example/file",
        "../file",
        key + "/../other",
        alice.prefix + "../file",
    ]:
        with pytest.raises(ValueError):
            alice.read(invalid)
        with pytest.raises(ValueError):
            alice.exists(invalid)
    with pytest.raises(ValueError):
        alice.put(b"bad", "../samples", ".parquet")
    (tmp_path / key).unlink()
    assert not alice.exists(key)
    with pytest.raises(FileNotFoundError):
        alice.read(key)


def test_local_object_symlink_cannot_escape_root(tmp_path):
    objects = _ObjectStore("alice", tmp_path / "objects")
    outside = tmp_path / "outside"
    outside.mkdir()
    (objects.root / "users").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        objects.put(b"data", "samples", ".parquet")


def test_vercel_never_falls_back_to_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(ValueError, match="Blob credentials"):
        _ObjectStore("alice", tmp_path / "forbidden")
    assert not (tmp_path / "forbidden").exists()


def test_explicit_local_object_storage_is_limited_to_vercel_dev(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_ENV", "development")
    monkeypatch.setenv("CYCLING_LOCAL_OBJECTS", "1")
    objects = _ObjectStore("alice", tmp_path / "local")
    assert objects.client is None
    assert objects.root == (tmp_path / "local").resolve()
    monkeypatch.setenv("VERCEL_ENV", "preview")
    with pytest.raises(ValueError, match="allowed only outside Vercel deployments"):
        _ObjectStore("alice", tmp_path / "forbidden")
    assert not (tmp_path / "forbidden").exists()


def test_private_blob_sdk_contract(monkeypatch, tmp_path):
    class BlobNotFoundError(Exception):
        pass

    client = Mock()
    client.get.return_value = SimpleNamespace(content=b"parquet bytes")
    sdk = SimpleNamespace(
        BlobClient=Mock(return_value=client), BlobNotFoundError=BlobNotFoundError
    )
    monkeypatch.setitem(sys.modules, "vercel", SimpleNamespace(blob=sdk))
    monkeypatch.setitem(sys.modules, "vercel.blob", sdk)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "test-only")
    objects = _ObjectStore("alice", tmp_path / "forbidden")
    key = objects.put(b"parquet bytes", "samples", ".parquet")
    client.put.assert_called_once_with(
        key,
        b"parquet bytes",
        access="private",
        add_random_suffix=False,
        overwrite=False,
    )
    assert objects.read(key) == b"parquet bytes"
    client.get.assert_called_once_with(key, access="private")
    assert objects.exists(key)
    client.head.assert_called_once_with(key)
    with pytest.raises(ValueError):
        objects.read("https://attacker.example/blob")
    assert client.get.call_count == 1
    client.head.side_effect = BlobNotFoundError()
    client.get.side_effect = BlobNotFoundError()
    assert not objects.exists(key)
    with pytest.raises(FileNotFoundError):
        objects.read(key)
    client.head.side_effect = RuntimeError("provider down")
    with pytest.raises(RuntimeError):
        objects.exists(key)
    objects.close()
    client.close.assert_called_once()
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize("count", [0, 1, 20])
def test_catalog_hydration_uses_one_owner_scoped_query_and_pins_paths(count):
    store = PostgresStore.__new__(PostgresStore)
    store.user_id = "alice"
    store._sample_paths = {}
    store.db = Mock()
    store.db.execute.return_value.fetchall.return_value = [
        {
            "id": str(index),
            "user_id": "alice",
            "sample_path": f"revision-{index}",
            "ingested_at": datetime(2026, 1, 1, tzinfo=UTC),
            "metadata": {"id": "forged", "sample_path": "forged", "rpe": 1},
            "context_id": f"context-{index}",
            "context": {"rpe": 5} if index else None,
            "quality_flags": ["a", "z"],
        }
        for index in range(count)
    ]
    activities = store.activities()
    store.db.execute.assert_called_once()
    sql, owners = store.db.execute.call_args.args
    assert owners == ("alice", "alice", "alice")
    assert sql.count("user_id=%s") == 3
    assert sql.endswith("ORDER BY a.start_time DESC, a.id")
    assert [a["id"] for a in activities] == [str(index) for index in range(count)]
    assert store._sample_paths == {
        str(index): f"revision-{index}" for index in range(count)
    }
    for index, activity in enumerate(activities):
        assert activity["sample_path"] == f"revision-{index}"
        assert activity["ingested_at"] == "2026-01-01T00:00:00+00:00"
        assert activity["rpe"] == (5 if index else 1)
        assert activity["context_id"] == f"context-{index}"
        assert activity["quality_flags"] == ["a", "z"]
        assert "user_id" not in activity


@pytest.fixture
def stores(tmp_path):
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "Set TEST_DATABASE_URL to run PostgreSQL integration tests; see module docstring"
        )
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    schema = "cycling_test_" + uuid4().hex
    active = []
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            dsn = make_conninfo(database_url, options=f"-csearch_path={schema}")
            with psycopg.connect(dsn, autocommit=True) as connection:
                apply_migrations(connection)

            def factory(user_id):
                with psycopg.connect(dsn, autocommit=True) as connection:
                    connection.execute(
                        "INSERT INTO cycling_users (user_id,google_sub,email,email_verified) "
                        "VALUES (%s,%s,%s,true) ON CONFLICT (user_id) DO NOTHING",
                        (user_id, user_id, f"{user_id}@example.test"),
                    )
                store = PostgresStore(dsn, user_id, object_root=tmp_path)
                store._test_dsn = dsn
                active.append(store)
                return store

            yield factory
        finally:
            for store in active:
                store.close()
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def upload(store, filename="ride-123456789.tcx", force=False):
    return store.ingest_upload(filename, tcx_text().encode(), force=force)


def test_upload_roundtrip_dedupe_service_and_provenance(stores):
    store = stores("alice")
    assert store.parameters() == (None, None)
    assert store.parameter_history() == []
    first = upload(store)
    ident = first["activity_id"]
    assert first["ingested"] is True
    assert ident == hashlib.sha256(tcx_text().encode()).hexdigest()
    activity = store.activity(ident)
    assert activity["strava_id_hint"] == "123456789"
    assert store.objects.read(activity["original_path"]) == tcx_text().encode()
    samples = store.samples(ident)
    assert samples and samples[0]["power_w"] == 200
    assert set(store.samples(ident, columns=["elapsed_s", "power_w"])[0]) == {
        "elapsed_s",
        "power_w",
    }
    second = upload(store, "alias.tcx")
    assert second["ingested"] is False
    assert second["activity_id"] == ident
    assert store.activity(ident)["sample_path"] == activity["sample_path"]
    pid = store.add_parameters(AthleteParameters(ftp_w=250))
    service = CyclingService(store)
    request = ActivityRequest(activity_id=ident)
    result = service.analyze_activity(request)
    assert result.parameter_id == pid
    assert result.data["activity"]["sample_path"] == activity["sample_path"]
    assert service.analyze_activity(request) == result
    hr_result = service.hr_distribution(request)
    assert [zone["label"] for zone in hr_result.data["zones"]] == [
        "HR Z1",
        "HR Z2",
        "HR Z3",
        "HR Z4",
        "HR Z5",
    ]
    weekly = service.weekly_cycling_training(date(2026, 1, 2))
    assert [zone["label"] for zone in weekly.data["zones"]] == [
        "HR Z1",
        "HR Z2",
        "HR Z3",
        "HR Z4",
        "HR Z5",
    ]
    assert store.status()["metric_snapshots"] == 1
    assert len(store.status()["duplicate_hashes"]) == 1
    assert stores("alice").samples(ident) == samples
    assert store.status()["activities"] == 1


def test_duplicate_blob_upload_is_removed_after_owner_scoped_dedupe(stores):
    store = stores("alice")
    first = upload(store)
    content = tcx_text().encode()
    duplicate_key = store.objects.put(content, "originals", ".tcx")

    result = store.ingest_blob("same-ride.tcx", duplicate_key)

    assert result["ingested"] is False
    assert result["activity_id"] == first["activity_id"]
    assert not store.objects.exists(duplicate_key)
    assert store.status()["activities"] == 1


def test_overview_keeps_warm_load_cache_blob_reads_and_latest_curve_anchor(
    stores, monkeypatch
):
    store = stores("alice")
    ident = upload(store)["activity_id"]
    store.add_parameters(AthleteParameters(ftp_w=250))
    service = CyclingService(store)
    service.analyze_activity(ActivityRequest(activity_id=ident))
    sample_reads = Mock(wraps=store.samples)
    monkeypatch.setattr(store, "samples", sample_reads)
    result = service.load(
        LoadRequest(start=date(2026, 1, 1), end=date(2026, 1, 2), modality="all")
    )
    assert result.data["activities"][0]["activity_id"] == ident
    sample_reads.assert_not_called()
    service.weekly_cycling_training(date(2026, 1, 2))
    sample_reads.assert_called_once_with(ident)
    sample_reads.reset_mock()
    curve = service.period_power_curve(
        PeriodPowerCurveRequest(period="90d", modality="all", durations=[1])
    )
    assert curve.data["activities_evaluated"] == 1
    assert curve.data["watts"]["1"] is not None
    sample_reads.assert_called_once_with(ident)
    outside = service.period_power_curve(
        PeriodPowerCurveRequest(period="90d", end_date=date(2035, 1, 1))
    )
    assert outside.data["activities_evaluated"] == 0


def test_every_public_lookup_and_write_is_owner_scoped(stores):
    alice, bob = stores("alice"), stores("bob")
    ident = upload(alice)["activity_id"]
    pid = alice.add_parameters(AthleteParameters(ftp_w=250))
    alice.save_metrics(ident, "shared-key", {"owner": "alice"})
    assert bob.activities() == []
    assert bob.parameters() == (None, None)
    assert bob.cached(ident, "shared-key") is None
    assert bob.status()["sources"] == []
    for read in [bob.activity, bob.samples]:
        with pytest.raises(KeyError):
            read(ident)
    with pytest.raises(KeyError):
        bob.set_context(ident, ActivityContext(rpe=5))
    with pytest.raises(KeyError):
        bob.save_metrics(ident, "shared-key", {})
    own = upload(bob)
    assert own["activity_id"] == ident  # Same bytes, different tenant rows and keys.
    assert bob.activity(ident)["sample_path"] != alice.activity(ident)["sample_path"]
    assert bob.cached(ident, "shared-key") is None
    bob.save_metrics(ident, "shared-key", {"owner": "bob"})
    assert alice.cached(ident, "shared-key") == {"owner": "alice"}
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        bob.save_metrics(ident, "bad-parameter", {"parameter_id": pid})
    assert bob.status()["metric_snapshots"] == 1
    # Values that look like SQL are still values.
    with pytest.raises(KeyError):
        bob.activity("' OR 1=1 --")


def test_batch_catalog_matches_single_reads_order_overlays_and_sample_revisions(
    stores, monkeypatch
):
    alice, bob = stores("alice"), stores("bob")
    original_id = upload(alice)["activity_id"]
    original, samples = alice.activity(original_id), alice.samples(original_id)
    for ident, start in [
        (original_id, "2026-01-01T00:00:00+00:00"),
        ("a", "2026-01-03T00:00:00+00:00"),
        ("b", "2026-01-03T00:00:00+00:00"),
    ]:
        alice.write_activity(
            ident,
            {
                **original,
                "source_hash": ident,
                "start_time": start,
                "quality_flags": ["z-flag", "a-flag"],
                "id": "forged",
                "sample_path": "forged",
                "rpe": 1,
            },
            samples,
        )
    recorded = datetime(2026, 2, 1, tzinfo=UTC)
    alice.import_context("a", "context-a", recorded, {"rpe": 3, "modality": "road"})
    alice.import_context("a", "context-z", recorded, {"rpe": 6, "modality": "mtb"})
    bob.write_activity(
        "a", {**original, "source_hash": "a", "quality_flags": ["bob-only"]}, samples
    )
    bob.import_context("a", "context-z", recorded, {"rpe": 9, "modality": "gravel"})
    expected = [alice.activity(ident) for ident in ("a", "b", original_id)]
    alice._sample_paths.clear()
    db, objects = Mock(wraps=alice.db), Mock(wraps=alice.objects)
    with monkeypatch.context() as patch:
        patch.setattr(alice, "db", db)
        patch.setattr(alice, "objects", objects)
        activities = alice.activities()
        db.execute.assert_called_once()
        objects.read.assert_not_called()
    assert activities == expected
    assert activities[0]["context_id"] == "context-z"
    assert activities[0]["rpe"] == 6
    assert activities[0]["modality"] == "mtb"
    assert activities[0]["quality_flags"] == ["a-flag", "z-flag"]
    assert bob.activities()[0]["rpe"] == 9
    assert bob.activities()[0]["quality_flags"] == ["bob-only"]
    assert alice._sample_paths == {a["id"]: a["sample_path"] for a in activities}
    writer = stores("alice")
    changed = [{**s, "power_w": 350.0} for s in samples]
    writer.write_activity("a", activities[0], changed)
    assert alice.samples("a") == samples  # Listing pinned the original revision.
    assert alice.activity("a")["sample_path"] != activities[0]["sample_path"]
    assert alice.samples("a") == changed


def test_disabled_accounts_cannot_open_a_tenant_store(stores):
    store = stores("alice")
    dsn, user_id = store._test_dsn, store.user_id
    store.db.execute(
        "UPDATE cycling_users SET is_active=false WHERE user_id=%s", (user_id,)
    )
    with pytest.raises(PermissionError, match="not active"):
        PostgresStore(dsn, user_id, object_root=store.objects.root)


def test_parameters_context_and_revisions(stores):
    store = stores("alice")
    old = store.add_parameters(
        AthleteParameters(effective_date=date(2026, 1, 1), ftp_w=250)
    )
    future = store.add_parameters(
        AthleteParameters(effective_date=date(2035, 1, 1), ftp_w=300)
    )
    assert store.parameters(date(2025, 1, 1))[0] == old
    assert store.parameters("2026-06-01")[0] == old
    assert store.parameters(mode="current")[0] == future
    assert len(store.parameter_history()) == 2
    ident = upload(store)["activity_id"]
    original = store.activity(ident)
    store.set_context(ident, ActivityContext(rpe=4))
    ctx = store.set_context(ident, ActivityContext(modality="mtb"))
    assert store.activity(ident)["rpe"] == 4
    assert store.activity(ident)["context_id"] == ctx
    upload(store, force=True)
    updated = store.activity(ident)
    assert updated["sample_path"] != original["sample_path"]
    assert updated["original_path"] == original["original_path"]
    assert updated["modality"] == "mtb"
    assert store.objects.exists(original["sample_path"])


def test_publish_failure_metadata_guards_and_hash_alias(stores, monkeypatch):
    store = stores("alice")
    ident = upload(store)["activity_id"]
    activity, samples = store.activity(ident), store.samples(ident)
    metadata = {
        **activity,
        "id": "forged",
        "sample_path": "https://attacker.invalid/file",
    }
    assert store.write_activity("another-id", metadata, samples) == ident
    assert store.activity(ident)["id"] == ident
    assert store.activity(ident)["sample_path"].startswith(store.objects.prefix)
    assert len(store.activities()) == 1
    previous = store.activity(ident)["sample_path"]
    monkeypatch.setattr(
        "cycling.postgres_store.pq.write_table", Mock(side_effect=OSError("disk full"))
    )
    with pytest.raises(OSError):
        upload(store, force=True)
    assert store.activity(ident)["sample_path"] == previous
    assert store.samples(ident) == samples
    assert store.status()["source_counts"] == {"error": 1}


def test_missing_files_and_invalid_upload(stores):
    store = stores("alice")
    ident = upload(store)["activity_id"]
    key = store.activity(ident)["sample_path"]
    (store.objects.root / key).unlink()
    assert store.status()["missing_samples"] == [ident]
    with pytest.raises(FileNotFoundError):
        store.samples(ident)
    assert upload(store)["ingested"]
    assert store.status()["missing_samples"] == []
    for name in ["../ride.tcx", "https://attacker.example/file.tcx", "ride.exe"]:
        with pytest.raises(ValueError):
            store.ingest_upload(name, b"bad")
    with pytest.raises(ParseError):
        store.ingest_upload("broken.tcx", b"not XML")
    assert store.status()["activities"] == 1
    assert store.status()["source_counts"] == {"error": 1, "ready": 1}


def test_concurrent_dedupe_and_revision_pinned_samples(stores):
    first, second = stores("alice"), stores("alice")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, [first, second]))
    assert sorted(r["ingested"] for r in results) == [False, True]
    ident = results[0]["activity_id"]
    activity, samples = first.activity(ident), first.samples(ident)
    changed = [{**s, "power_w": 350.0} for s in samples]
    second.write_activity(ident, activity, changed)
    # The reader's existing cache key still names the original samples.
    assert first.samples(ident) == samples
    current = first.activity(ident)
    assert current["sample_path"] != activity["sample_path"]
    assert first.samples(ident) == changed
    first.save_metrics(ident, "old-revision", {"data": {"activity": activity}})
    snapshot = first.db.execute(
        "SELECT sample_path FROM cycling_metrics WHERE user_id=%s AND cache_key=%s",
        (first.user_id, "old-revision"),
    ).fetchone()
    assert snapshot["sample_path"] == activity["sample_path"]


def test_failed_catalog_publication_rolls_back_all_metadata(stores):
    import psycopg

    store = stores("alice")
    ident = upload(store)["activity_id"]
    before, samples = store.activity(ident), store.samples(ident)
    invalid = {
        **before,
        "quality_flags": ["changed"],
        "laps": [{"lap_index": 1}, {"lap_index": 1}],
    }
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.write_activity(ident, invalid, samples)
    assert store.activity(ident) == before
    files = store.db.execute(
        "SELECT object_key FROM cycling_files WHERE user_id=%s",
        (store.user_id,),
    ).fetchall()
    assert {f["object_key"] for f in files} == {
        before["sample_path"],
        before["original_path"],
    }


def test_legacy_migration_is_dry_run_first_and_preserves_history(stores, tmp_path):
    from scripts.migrate_legacy_catalog import migrate

    source = tmp_path / "ride-123456789.tcx"
    source.write_text(tcx_text())
    legacy_root = tmp_path / "legacy-catalog"
    with Store(legacy_root) as legacy:
        assert ingest(legacy, source)["ingested"] == 1
        legacy.add_parameters(AthleteParameters(ftp_w=250))
        activity_id = legacy.activities()[0]["id"]
        legacy.set_context(activity_id, ActivityContext(rpe=5))
        expected_samples = legacy.samples(activity_id)

    target = stores("alice")
    dry_run = migrate(
        legacy_root, target._test_dsn, "alice@example.test", False, True, tmp_path
    )
    assert dry_run["activities_discovered"] == 1
    assert dry_run["activities_imported"] == 0
    assert target.activities() == []

    result = migrate(
        legacy_root,
        target._test_dsn,
        "alice@example.test",
        True,
        True,
        tmp_path,
        precompute_metrics=True,
    )
    assert result["activities_imported"] == 1
    assert result["metric_snapshots_precomputed"] == 1
    migrated = target.activity(activity_id)
    assert target.samples(activity_id) == expected_samples
    assert migrated["rpe"] == 5
    assert target.objects.read(migrated["original_path"]) == source.read_bytes()
    assert target.parameters()[1].ftp_w == 250

    rerun = migrate(
        legacy_root, target._test_dsn, "alice@example.test", True, True, tmp_path
    )
    assert rerun["activities_imported"] == 0
    assert rerun["activities_skipped"] == 1
    assert rerun["contexts_imported"] == 0
    assert target.status()["activities"] == 1
