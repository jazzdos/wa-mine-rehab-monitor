"""Tests for the bounded HTTP client (D13 Batch C task C1).

Every test drives the client through an injected fake session and an
injected recording sleep -- no live network, no real waiting.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import requests

from wa_mine_monitor.http import (
    HttpClient,
    HttpRequestRefused,
    HttpRetryExhausted,
    RetryPolicy,
)


class FakeResponse:
    def __init__(self, status_code: int, *, headers=None, json_body=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self._json_body = json_body
        self.text = text
        self.content = text.encode("utf-8")

    def json(self):
        return self._json_body


class FakeSession:
    """Replays a scripted sequence of responses/exceptions, recording calls."""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[dict] = []

    def get(self, url, *, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class RecordingSleep:
    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _client(script, *, policy=None, now=None):
    session = FakeSession(script)
    sleep = RecordingSleep()
    client = HttpClient(
        policy or RetryPolicy(),
        session=session,
        sleep=sleep,
        now=now or (lambda: datetime(2026, 8, 16, 0, 0, 0, tzinfo=UTC)),
    )
    return client, session, sleep


def test_success_first_attempt_no_sleep():
    client, session, sleep = _client([FakeResponse(200, json_body={"ok": True})])
    response = client.get("https://example.test/stac")
    assert response.status_code == 200
    assert sleep.delays == []
    assert len(session.calls) == 1


def test_429_numeric_retry_after_is_honoured():
    client, _, sleep = _client(
        [
            FakeResponse(429, headers={"Retry-After": "7"}),
            FakeResponse(200, json_body={}),
        ]
    )
    client.get("https://example.test/stac")
    assert sleep.delays == [7.0]


def test_429_http_date_retry_after_is_honoured():
    # now is pinned to 2026-08-16T00:00:00Z; the date is 20 s later.
    client, _, sleep = _client(
        [
            FakeResponse(429, headers={"Retry-After": "Sun, 16 Aug 2026 00:00:20 GMT"}),
            FakeResponse(200, json_body={}),
        ]
    )
    client.get("https://example.test/stac")
    assert sleep.delays == [20.0]


def test_retry_after_is_capped():
    policy = RetryPolicy(retry_after_cap_seconds=10.0)
    client, _, sleep = _client(
        [
            FakeResponse(429, headers={"Retry-After": "3600"}),
            FakeResponse(200, json_body={}),
        ],
        policy=policy,
    )
    client.get("https://example.test/stac")
    assert sleep.delays == [10.0]


def test_http_date_in_the_past_sleeps_zero_not_negative():
    client, _, sleep = _client(
        [
            FakeResponse(429, headers={"Retry-After": "Sat, 15 Aug 2026 23:59:00 GMT"}),
            FakeResponse(200, json_body={}),
        ]
    )
    client.get("https://example.test/stac")
    assert sleep.delays == [0.0]


def test_connection_error_is_retried():
    client, session, sleep = _client(
        [requests.ConnectionError("reset"), FakeResponse(200, json_body={})]
    )
    response = client.get("https://example.test/stac")
    assert response.status_code == 200
    assert len(session.calls) == 2
    assert len(sleep.delays) == 1


def test_timeout_is_retried():
    client, session, _ = _client([requests.Timeout("slow"), FakeResponse(200, json_body={})])
    assert client.get("https://example.test/stac").status_code == 200
    assert len(session.calls) == 2


def test_500_is_retried_with_backoff():
    client, _, sleep = _client([FakeResponse(500), FakeResponse(200, json_body={})])
    client.get("https://example.test/stac")
    assert sleep.delays == [1.0]  # 2**0 on attempt 0


def test_non_429_4xx_refuses_immediately_without_retry():
    client, session, sleep = _client([FakeResponse(404)])
    with pytest.raises(HttpRequestRefused) as excinfo:
        client.get("https://example.test/stac/collections/nope")
    assert "404" in str(excinfo.value)
    assert len(session.calls) == 1
    assert sleep.delays == []


def test_exhausted_attempts_raise_without_query_secrets():
    policy = RetryPolicy(max_attempts=2)
    client, _, _ = _client([FakeResponse(500), FakeResponse(500)], policy=policy)
    with pytest.raises(HttpRetryExhausted) as excinfo:
        client.get(
            "https://user:SECRETTOKEN@example.test/stac?api_key=SECRETTOKEN&b=2",
            params={"token": "SECRETTOKEN"},
        )
    message = str(excinfo.value)
    assert "SECRETTOKEN" not in message
    assert "api_key" not in message
    assert "example.test" in message  # host survives so the error is actionable
    assert "2 attempt" in message


def test_exhausted_after_retryable_exception_names_the_exception_type():
    policy = RetryPolicy(max_attempts=1)
    client, _, _ = _client([requests.ConnectionError("reset")], policy=policy)
    with pytest.raises(HttpRetryExhausted) as excinfo:
        client.get("https://example.test/stac")
    assert "ConnectionError" in str(excinfo.value)


def test_non_retryable_exception_propagates():
    client, _, _ = _client([ValueError("bug in caller")])
    with pytest.raises(ValueError):
        client.get("https://example.test/stac")


def test_get_json_returns_parsed_body():
    client, _, _ = _client([FakeResponse(200, json_body={"type": "Collection"})])
    assert client.get_json("https://example.test/c") == {"type": "Collection"}


def test_get_text_and_get_bytes():
    client, _, _ = _client([FakeResponse(200, text="hello"), FakeResponse(200, text="hello")])
    assert client.get_text("https://example.test/t") == "hello"
    assert client.get_bytes("https://example.test/t") == b"hello"


def test_convenience_methods_share_the_retry_loop():
    client, session, _ = _client([FakeResponse(500), FakeResponse(200, json_body={"ok": 1})])
    assert client.get_json("https://example.test/c") == {"ok": 1}
    assert len(session.calls) == 2


import threading

from wa_mine_monitor.http import map_concurrent


def test_serial_and_parallel_results_are_identical_and_ordered():
    items = list(range(20))
    serial = map_concurrent(lambda x: x * x, items, max_workers=1)
    parallel = map_concurrent(lambda x: x * x, items, max_workers=4)
    assert serial == parallel == [x * x for x in items]


def test_worker_count_is_bounded():
    active = 0
    peak = 0
    lock = threading.Lock()
    barrier = threading.Event()

    def tracked(x):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=0.05)
        with lock:
            active -= 1
        return x

    map_concurrent(tracked, list(range(12)), max_workers=3)
    assert peak <= 3


def test_lowest_index_exception_wins_deterministically():
    def maybe_fail(x):
        if x in (3, 7):
            raise ValueError(f"boom-{x}")
        return x

    with pytest.raises(ValueError, match="boom-3"):
        map_concurrent(maybe_fail, list(range(10)), max_workers=4)


def test_max_workers_one_runs_inline_on_the_calling_thread():
    thread_ids = []

    def record(x):
        thread_ids.append(threading.get_ident())
        return x

    map_concurrent(record, [1, 2, 3], max_workers=1)
    assert set(thread_ids) == {threading.get_ident()}


def test_tolerate_errors_yields_none_per_failure_in_order():
    def maybe_fail(x):
        if x % 2:
            raise ValueError("odd")
        return x

    result = map_concurrent(maybe_fail, [0, 1, 2, 3], max_workers=2, tolerate_errors=True)
    assert result == [0, None, 2, None]


def test_tolerate_errors_defaults_to_false_so_errors_propagate():
    # `1 / 0` raises ZeroDivisionError, not ValueError -- assert the type the
    # callable actually raises so this step can go green.
    with pytest.raises(ZeroDivisionError):
        map_concurrent(lambda x: 1 / 0, [1], max_workers=2)
