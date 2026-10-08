"""Database reconnect behavior for long-running worker entry points."""

import threading

import psycopg
import pytest

from agents_service.__main__ import _run_with_database_retry


class StopAfterWait:
    """Test stop event that records waits without sleeping."""

    def __init__(self, *, stop_on_wait: bool = False) -> None:
        self.waits: list[float] = []
        self.stop_on_wait = stop_on_wait

    def is_set(self) -> bool:
        return False

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        return self.stop_on_wait


class FlakyWorker:
    """Worker fake that raises queued errors and then exits cleanly."""

    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        self.calls = 0

    def run(self, _stop: threading.Event, *, poll_seconds: float) -> None:
        assert poll_seconds == 2
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)


def test_database_connection_errors_retry_with_capped_backoff() -> None:
    worker = FlakyWorker([psycopg.OperationalError(), psycopg.InterfaceError()] * 4)
    stop = StopAfterWait()

    _run_with_database_retry(worker, stop, poll_seconds=2)

    assert worker.calls == 9
    assert stop.waits == [1, 2, 4, 8, 16, 30, 30, 30]


def test_database_retry_stops_when_shutdown_arrives_during_backoff() -> None:
    worker = FlakyWorker([psycopg.OperationalError()])
    stop = StopAfterWait(stop_on_wait=True)

    _run_with_database_retry(worker, stop, poll_seconds=2)

    assert worker.calls == 1
    assert stop.waits == [1]


def test_non_connection_errors_are_not_swallowed() -> None:
    worker = FlakyWorker([ValueError("invalid worker configuration")])

    with pytest.raises(ValueError, match="invalid worker configuration"):
        _run_with_database_retry(worker, StopAfterWait(), poll_seconds=2)

    assert worker.calls == 1
