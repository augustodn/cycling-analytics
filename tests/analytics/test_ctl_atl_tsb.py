"""Synthetic vector tests for CTL/ATL/TSB recursive model."""

from datetime import date

import pytest

from cycling.analytics.load import training_load


def test_ctl_atl_tsb_decay_and_tsb_prior_day_state():
    daily_load = {"2026-01-01": 100.0}
    start = date(2026, 1, 1)
    end = date(2026, 1, 3)

    rows = training_load(daily_load, start, end, ctl_days=2, atl_days=2)
    assert len(rows) == 3

    # Day 1: 2026-01-01
    assert rows[0]["date"] == "2026-01-01"
    assert rows[0]["known"] is True
    assert rows[0]["load"] == 100.0
    assert rows[0]["ctl"] == 50.0
    assert rows[0]["atl"] == 50.0
    assert rows[0]["tsb"] == 0.0  # prior state was (0 - 0)

    # Day 2: 2026-01-02 (missing day -> effective load 0.0)
    assert rows[1]["date"] == "2026-01-02"
    assert rows[1]["known"] is False
    assert rows[1]["load"] is None
    assert rows[1]["ctl"] == 25.0  # 50 + (0 - 50)/2
    assert rows[1]["atl"] == 25.0
    assert rows[1]["tsb"] == 0.0  # prior state (50 - 50)


def test_ctl_atl_tsb_validates_inputs():
    daily_load = {}
    d1 = date(2026, 1, 1)
    d2 = date(2026, 1, 2)

    with pytest.raises(ValueError):
        training_load(daily_load, d2, d1)  # end before start

    with pytest.raises(ValueError):
        training_load(daily_load, d1, d2, ctl_days=0)

    with pytest.raises(ValueError):
        training_load(daily_load, d1, d2, atl_days=-1)
