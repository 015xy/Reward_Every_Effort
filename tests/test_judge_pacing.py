"""Bound retries and concurrent dispatches without calling a real provider."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime

import httpx
import pytest
from ree.judge import MAX_RETRY_DELAY, JudgeError, OpenAIJudge
from ree.models import Judgement


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []
        self.oversleep = 0

    def monotonic(self):
        return self.now

    def wall(self):
        return 1_795_000_000 + self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay + self.oversleep


@pytest.fixture
def clock(monkeypatch):
    value = FakeClock()
    monkeypatch.setattr("ree.judge.time.monotonic", value.monotonic)
    monkeypatch.setattr("ree.judge.time.time", value.wall)
    monkeypatch.setattr("ree.judge.time.sleep", value.sleep)
    return value


def success():
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"status": "ok", "value": True, "reason": "Mock response"}
                        )
                    }
                }
            ]
        },
    )


def client_for(handler, **kwargs):
    client = OpenAIJudge(model="mock", **kwargs)
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_seconds_recovers_and_reports_status(clock, status):
    starts = []

    def handler(_):
        starts.append(clock.now)
        return (
            httpx.Response(status, headers={"Retry-After": "2.5"})
            if len(starts) == 1
            else success()
        )

    client = client_for(handler, retries=1)
    try:
        assert client.judge("test", "Policy", {}, Judgement).value is True
        assert starts == [100, 102.5] and clock.sleeps == [2.5]
        assert client.usage[0]["http_status"] == status
        assert client.usage[1]["status"] == "ok"
    finally:
        client.close()


def test_retry_after_http_date_recovers(clock):
    date = format_datetime(datetime.fromtimestamp(clock.wall() + 3, timezone.utc), usegmt=True)
    calls = []

    def handler(_):
        calls.append(clock.now)
        return httpx.Response(429, headers={"Retry-After": date}) if len(calls) == 1 else success()

    client = client_for(handler, retries=1)
    try:
        client.judge("test", "Policy", {}, Judgement)
        assert calls == [100, 103] and clock.sleeps == [3]
    finally:
        client.close()


def test_long_retry_after_waits_full_interval_in_bounded_chunks(clock):
    calls = []

    def handler(_):
        calls.append(clock.now)
        return httpx.Response(503, headers={"Retry-After": "120"}) if len(calls) == 1 else success()

    client = client_for(handler, retries=1, deadline=200)
    try:
        client.judge("test", "Policy", {}, Judgement)
        assert clock.sleeps == [MAX_RETRY_DELAY, MAX_RETRY_DELAY]
        assert calls == [100, 220]
    finally:
        client.close()


def test_retry_after_beyond_deadline_does_not_sleep_or_send_again(clock):
    calls = []

    def handler(_):
        calls.append(clock.now)
        return httpx.Response(429, headers={"Retry-After": "60"})

    client = client_for(handler, retries=3, deadline=5)
    try:
        with pytest.raises(JudgeError, match="deadline.*HTTP 429"):
            client.judge("test", "Policy", {}, Judgement)
        assert calls == [100] and clock.sleeps == []
        assert client.usage[0]["http_status"] == 429
    finally:
        client.close()


@pytest.mark.parametrize("header", ["invalid", "-1", "inf", "NaN"])
def test_invalid_retry_after_uses_bounded_default_backoff(clock, header):
    calls = []

    def handler(_):
        calls.append(clock.now)
        return (
            httpx.Response(429, headers={"Retry-After": header}) if len(calls) == 1 else success()
        )

    client = client_for(handler, retries=1)
    try:
        client.judge("test", "Policy", {}, Judgement)
        assert clock.sleeps == [0.25] and len(calls) == 2
    finally:
        client.close()


def test_exhausted_http_retry_retains_status_without_service_secrets(clock):
    sensitive = "secret-service-body-and-key"
    client = client_for(
        lambda _: httpx.Response(
            429, headers={"Retry-After": "0", "Authorization": sensitive}, text=sensitive
        ),
        retries=1,
    )
    try:
        with pytest.raises(JudgeError, match="HTTP 429") as failure:
            client.judge("test", "Policy", {}, Judgement)
        assert sensitive not in str(failure.value)
        assert sensitive not in json.dumps(client.usage)
        assert [usage["http_status"] for usage in client.usage] == [429, 429]
        assert client._calls == 2
    finally:
        client.close()


def test_nonretryable_http_status_is_preserved_without_retry(clock):
    client = client_for(lambda _: httpx.Response(400, text="private service body"), retries=3)
    try:
        with pytest.raises(JudgeError, match="HTTP 400"):
            client.judge("test", "Policy", {}, Judgement)
        assert client._calls == 1 and client.usage[0]["http_status"] == 400
        assert clock.sleeps == []
    finally:
        client.close()


def test_budget_exhausted_between_retries_retains_http_status(clock):
    client = client_for(
        lambda _: httpx.Response(503, headers={"Retry-After": "0"}), retries=3, max_calls=1
    )
    try:
        with pytest.raises(JudgeError, match="budget.*HTTP 503"):
            client.judge("test", "Policy", {}, Judgement)
        assert client._calls == 1
    finally:
        client.close()


def test_pacing_is_shared_across_workers():
    starts, lock = [], threading.Lock()

    def handler(_):
        with lock:
            starts.append(time.monotonic())
        return success()

    client = client_for(handler, retries=0, min_interval=0.025)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(
                pool.map(
                    lambda i: client.judge(str(i), "Policy", {"index": i}, Judgement), range(4)
                )
            )
        assert all(value.value is True for value in values)
        assert len(starts) == 4 and client._calls == 4
        assert all(b - a >= 0.02 for a, b in zip(starts[:-1], starts[1:], strict=True))
    finally:
        client.close()


def test_cached_result_does_not_wait_for_another_workers_pacing(clock, monkeypatch):
    waiting, release = threading.Event(), threading.Event()

    def pause(delay):
        waiting.set()
        assert release.wait(timeout=2)
        clock.now += delay

    client = client_for(lambda _: success(), min_interval=5, retries=0)
    try:
        client.judge("prime", "Policy", {}, Judgement)
        monkeypatch.setattr("ree.judge.time.sleep", pause)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.judge, "new", "New policy", {}, Judgement)
            try:
                assert waiting.wait(timeout=2)
                assert client.judge("cached", "Policy", {}, Judgement).value is True
                assert client.usage[-1]["status"] == "cache_hit"
                assert not pending.done()
            finally:
                release.set()
            assert pending.result(timeout=2).value is True
    finally:
        client.close()


def test_pacing_beyond_deadline_does_not_start_a_request(clock):
    client = client_for(lambda _: success(), min_interval=5, retries=0, deadline=1)
    try:
        client.judge("first", "Policy", {}, Judgement)
        with pytest.raises(JudgeError, match="deadline"):
            client.judge("next", "Other policy", {}, Judgement)
        assert client._calls == 1 and clock.sleeps == []
    finally:
        client.close()


def test_clock_oversleep_does_not_dispatch_after_deadline(clock):
    client = client_for(lambda _: success(), min_interval=0.5, retries=0, deadline=1)
    try:
        client.judge("first", "Policy", {}, Judgement)
        clock.oversleep = 2
        with pytest.raises(JudgeError, match="deadline"):
            client.judge("next", "Other policy", {}, Judgement)
        assert client._calls == 1
    finally:
        client.close()


def test_waiting_for_pacing_lock_obeys_deadline():
    client = client_for(lambda _: success(), retries=0, deadline=0.03)
    client._pacing_lock.acquire()
    started = time.monotonic()
    try:
        with pytest.raises(JudgeError, match="deadline"):
            client.judge("test", "Policy", {}, Judgement)
        assert time.monotonic() - started < 0.2 and client._calls == 0
    finally:
        client._pacing_lock.release()
        client.close()


def test_subsecond_remaining_deadline_is_not_rounded_up(clock):
    timeouts = []

    def handler(request):
        timeouts.append(request.extensions["timeout"]["read"])
        return success()

    client = client_for(handler, retries=0, deadline=0.025)
    try:
        client.judge("test", "Policy", {}, Judgement)
        assert timeouts[0] > 0 and timeouts[0] == pytest.approx(0.025)
    finally:
        client.close()


def test_transport_returning_after_deadline_cannot_succeed_or_retry(clock):
    def handler(_):
        clock.now += 2
        return success()

    client = client_for(handler, retries=3, deadline=1)
    try:
        with pytest.raises(JudgeError, match="deadline"):
            client.judge("test", "Policy", {}, Judgement)
        assert client._calls == 1
        assert client.usage[0]["http_status"] is None
        assert clock.sleeps == []
    finally:
        client.close()


def test_large_finite_deadline_does_not_overflow_lock_timeout(clock):
    client = client_for(lambda _: success(), retries=0, deadline=1e300)
    try:
        assert client.judge("test", "Policy", {}, Judgement).value is True
    finally:
        client.close()


@pytest.mark.parametrize("interval", [-1, float("inf"), float("nan")])
def test_invalid_pacing_interval_is_rejected(interval):
    with pytest.raises(ValueError):
        OpenAIJudge(model="mock", min_interval=interval)
