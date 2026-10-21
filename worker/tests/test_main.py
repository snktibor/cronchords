import signal
from datetime import timedelta

import pytest

from cronchords.__main__ import keep_syncing, parse_args


class FakeTime:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_syncs_start_every_interval_however_long_they_take():
    time = FakeTime()
    started = []

    def sync():
        started.append(time.now)
        time.now += 100
        if len(started) == 3:
            signal.raise_signal(signal.SIGTERM)

    keep_syncing(sync, timedelta(hours=1), clock=time.clock, sleep=time.sleep)

    assert started == [0, 3600, 7200]


def test_a_sync_longer_than_the_interval_is_followed_right_away_but_only_once():
    time = FakeTime()
    started = []

    def sync():
        started.append(time.now)
        time.now += 200 if len(started) == 1 else 10
        if len(started) == 3:
            signal.raise_signal(signal.SIGTERM)

    keep_syncing(sync, timedelta(minutes=1), clock=time.clock, sleep=time.sleep)

    assert started == [0, 200, 260]


def test_a_failed_sync_doesnt_stop_the_next_ones(caplog):
    time = FakeTime()
    calls = 0

    def sync():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("something unexpected")
        signal.raise_signal(signal.SIGTERM)

    keep_syncing(sync, timedelta(hours=1), clock=time.clock, sleep=time.sleep)

    assert calls == 2
    assert "something unexpected" in caplog.text


def test_sigterm_stops_the_wait_for_the_next_sync():
    time = FakeTime()
    calls = 0

    def sync():
        nonlocal calls
        calls += 1

    def sleep(seconds):
        time.sleep(seconds)
        if time.now >= 60:
            signal.raise_signal(signal.SIGTERM)

    keep_syncing(sync, timedelta(hours=1), clock=time.clock, sleep=sleep)

    assert calls == 1
    assert time.now == 60


def test_sigterm_during_a_sync_lets_it_finish():
    time = FakeTime()
    finished = []
    handler = signal.getsignal(signal.SIGTERM)

    def sync():
        signal.raise_signal(signal.SIGTERM)
        finished.append(time.now)

    keep_syncing(sync, timedelta(hours=1), clock=time.clock, sleep=time.sleep)

    assert finished == [0]
    assert signal.getsignal(signal.SIGTERM) is handler


def test_every_takes_whole_minutes():
    assert parse_args([]).every is None
    assert parse_args(["--every", "15"]).every == timedelta(minutes=15)
    for value in ("0", "-5", "1.5", "hourly"):
        with pytest.raises(SystemExit):
            parse_args(["--every", value])
