"""Synthetic vector tests for Durability analysis across cumulative work buckets."""

from cycling.analytics.durability import durability


def make_power_rows(power, start_s=0, active=True):
    return [
        {
            "elapsed_s": start_s + i,
            "active": active,
            "segment": 0,
            "power_w": p,
        }
        for i, p in enumerate(power)
    ]


def test_durability_baseline_and_decay():
    # 20 samples of 100W -> total work = 2000 J = 2.0 kJ
    samples = make_power_rows([100] * 20)
    res = durability(samples, durations=[5], bucket_kj=[0, 0.5])

    assert res["available"] is True
    assert res["total_work_kj"] == 2.0
    b0 = res["buckets"][0]
    b1 = res["buckets"][1]
    assert b0["best_w"][5] == 100.0
    assert b0["change_percent"][5] == 0.0
    assert b1["best_w"][5] == 100.0


def test_durability_no_power_activity_explicitly_unavailable():
    # Activity without power stream (e.g. power_w is None or absent)
    samples = [
        {"elapsed_s": i, "active": True, "segment": 0, "power_w": None}
        for i in range(10)
    ]
    res = durability(samples, durations=[5], bucket_kj=[0, 100])
    assert res["available"] is False
    assert "power coverage" in res["reason"] or "missing power" in res["reason"]
    assert res["buckets"] == []


def test_durability_unexplained_gap():
    # Gap in elapsed_s without inactive pause row
    samples = make_power_rows([100] * 5, start_s=0) + make_power_rows(
        [100] * 5, start_s=10
    )
    res = durability(samples, durations=[5], bucket_kj=[0])
    assert res["available"] is False
    assert "unexplained elapsed gap" in res["reason"]


def test_durability_allows_explicit_inactive_pause():
    # Explicit inactive pause rows bridging the gap
    samples = (
        make_power_rows([100] * 5, start_s=0, active=True)
        + make_power_rows([None] * 2, start_s=5, active=False)
        + make_power_rows([100] * 5, start_s=7, active=True)
    )
    res = durability(samples, durations=[5], bucket_kj=[0])
    assert res["available"] is True
    assert res["exposure_seconds"] == 10
