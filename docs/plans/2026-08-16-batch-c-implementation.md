# Batch C Implementation Plan — DEA catalogue, epoch coverage, and volume re-derivation

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Execute D13 §3 (Batch C): adapt the bounded HTTP client, capture an
immutable pinned DEA STAC catalogue snapshot, derive per-site epoch coverage
into an enriched versioned register, and re-derive the Tier 1 volume estimate
from real populations — replacing the provisional 367-tile / 350 GB / 2.3 TB
planning figures.

**Architecture:** Five new modules (`http.py`, `source_catalogue.py`,
`sources/dea.py`, `dea_coverage.py`, `dea_volume.py`) plus three new CLI
commands (`fetch-dea-catalogue`, `build-dea-coverage`, `derive-dea-volume`)
following the established snapshot → validate → finalize → manifest lifecycle
(`cli.py`'s `fetch_tenements` is the template). The HTTP client is an
**adaptation** of the dataplatform repo's `core/http.py` closing its three
measured gaps (no retry of connection/timeout exceptions, HTTP-date
`Retry-After` ignored, aggregate concurrency left to callers). Everything else
is **built** per the D13 reuse adjudication — no dataplatform schema, driver,
or backfill framework is ported.

**Tech Stack:** Python 3.12, requests (injectable sessions — no live network
in any test), pandas/pyarrow (declared schemas, nullable `Int64`), Typer CLI,
existing `snapshots`/`manifests`/`licence`/`secrets`/`export_gate` modules.

---

> **Amendment history:** a detached codex plan attack (2026-08-16) returned
> three finding clusters; all were checked against the real modules and all
> stood. They are APPLIED in the tasks below — the estimator is sized from
> Maus footprint scalars under a declared `WindowPolicy` (Tasks 12–15), the
> catalogue summary carries `reported_item_count` / `required_assets` /
> `collection_response_sha256` (Tasks 5–7), the item index carries
> `collection_id` and `asset_identity` (Task 8), the `root_relative_path`
> call sites match the module's real signature and every manifest ingredient
> is computed before the artefact is written (Tasks 11, 15), and three red
> steps that would have failed for the wrong reason are corrected (Tasks 3,
> 7, 10, 15). The footprint-artefact direction (Tasks 12–13) was decided by
> a second codex consult and accepted; see
> `docs/decisions/2026-08-16-batch-c-footprint-input-direction.md`.


## Conventions binding every task (from the repo's own tree — verify there, not here)

- Fixture-first TDD; tests never touch the network. HTTP is tested through
  injected fake sessions; STAC payloads are committed synthetic fixtures.
- Dated snapshots: `<data_root>/raw/<source_id>/<date>/` with `metadata.txt` +
  `SHA256SUMS.txt` via `snapshots.write_snapshot_metadata` /
  `finalize_snapshot` / `verify_snapshot`.
- Immutable run manifests via `manifests.write_run_manifest(output=...)`;
  the manifest records `output.sha256` — that is the digest C4/C5 verify.
- Declared Arrow schemas only (`tables.write_table`); null = not computed vs.
  genuine zero uses nullable `"Int64"` (the `n_tenements_intersecting`
  pattern, register.py — D12.2).
- Diagnostics are fixed-key count dicts, never booleans
  (`tenement_count_disclosure` is the template).
- Structured JSON refusals + `typer.Exit(1)`; reuse `cli.py` helpers
  (`_load_config_or_exit`, `_refuse_if_snapshot_already_finalized`,
  `_refuse_if_curated_output_already_exists`, `_latest_curated_dated_dir`,
  `_verify_snapshot_or_refuse`, `_write_table_or_refuse`,
  `_collect_git_state_disclosing_gaps`, `ConfigOption`, `DateOption`).
- `--date` always explicit, never computed from the clock.
- Secrets: errors/manifests/echoes never carry credential-bearing query
  values (`secrets.scrub_url_secrets` and friends).
- Quality battery after every task: `uv run ruff check src tests && uv run
  ruff format --check src tests && uv run mypy src && uv run pytest -q`.
- No commit steps anywhere in this plan.

D13 test-mapping note: D13 lists "a completeness-sensitive caller refusing
`tolerate_errors=True`" under C1's tests. The real completeness-sensitive
caller is the C2 catalogue fetch, so that test lands in
`tests/sources/test_dea.py` (Task 7), while `tests/test_http.py` pins that
`tolerate_errors` defaults to False and errors propagate by default. Both
halves exist; neither is skipped.

---

### Task 1: `RetryPolicy` and `HttpClient.get`

**Files:**
- Create: `src/wa_mine_monitor/http.py`
- Create: `tests/test_http.py`

**Step 1: Write the failing tests**

```python
# tests/test_http.py
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
        self.calls.append(
            {"url": url, "params": params, "headers": headers, "timeout": timeout}
        )
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
            FakeResponse(
                429, headers={"Retry-After": "Sun, 16 Aug 2026 00:00:20 GMT"}
            ),
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
            FakeResponse(
                429, headers={"Retry-After": "Sat, 15 Aug 2026 23:59:00 GMT"}
            ),
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
    client, session, _ = _client(
        [requests.Timeout("slow"), FakeResponse(200, json_body={})]
    )
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
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_http.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'wa_mine_monitor.http'`

**Step 3: Write the implementation**

```python
# src/wa_mine_monitor/http.py
"""Bounded HTTP client with declared retry policy, and ordered concurrency.

Adapted from the dataplatform repo's ``core/http.py`` under the D13 §3 reuse
adjudication (**adapt**), closing that implementation's three measured gaps:

1. it did not retry ``requests.ConnectionError``/``requests.Timeout`` -- a
   transient transport failure aborted a whole catalogue fetch;
2. it ignored the HTTP-date form of ``Retry-After`` (falling back silently
   to exponential backoff -- a library default inherited as a decision);
3. it left aggregate concurrency to call sites, so no source declared one.

Every knob lives in a frozen ``RetryPolicy`` a source declares once. The
session, the sleep function and the clock are all injectable so tests drive
the full retry loop without a network or a real wait. Exhausted-attempt
errors carry the URL with userinfo and query string REMOVED -- query values
can carry credentials (SILO's API key travels as a query param), and an
exception message ends up in logs and structured refusals.
"""

from __future__ import annotations

import email.utils
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests


@dataclass(frozen=True)
class RetryPolicy:
    """One source's declared transport policy.

    ``max_workers`` is the source-level AGGREGATE concurrency: the value a
    caller must pass to ``map_concurrent`` when fanning out over this
    source, so the limit is declared here once rather than invented at each
    call site (D13 C1 acceptance).
    """

    max_attempts: int = 6
    timeout_seconds: float = 60.0
    backoff_cap_seconds: float = 30.0
    retry_after_cap_seconds: float = 60.0
    retryable_exceptions: tuple[type[BaseException], ...] = (
        requests.ConnectionError,
        requests.Timeout,
    )
    max_workers: int = 4


class HttpRequestRefused(RuntimeError):
    """A non-retryable response (non-429 4xx) was refused immediately."""


class HttpRetryExhausted(RuntimeError):
    """Every attempt failed; the message names the redacted URL only."""


def redacted_url(url: str) -> str:
    """Drop userinfo, query and fragment; keep scheme, host and path.

    The query string is removed WHOLE rather than scrubbed key-by-key:
    a key-name scrubber must guess which keys are credentials, and a wrong
    guess here leaks into every log line. Host and path survive so the
    error stays actionable.
    """
    parts = urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


class HttpClient:
    """GET with bounded retries under a declared ``RetryPolicy``.

    - 429: honour ``Retry-After`` (numeric seconds OR HTTP-date), capped.
    - >=500 and declared retryable exceptions: exponential backoff, capped.
    - other 4xx: refuse immediately -- retrying a 404 is asking the same
      wrong question faster.
    """

    def __init__(
        self,
        policy: RetryPolicy | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._policy = policy or RetryPolicy()
        self._headers = dict(headers or {})
        self._session = session if session is not None else requests.Session()
        self._sleep = sleep
        # Injectable clock: used ONLY for HTTP-date Retry-After arithmetic,
        # never for artefact dates (those are always explicit ``--date``).
        self._now = now or (lambda: datetime.now(UTC))

    def get(self, url: str, *, params: Mapping[str, Any] | None = None) -> Any:
        policy = self._policy
        last_status: int | None = None
        last_exception_name: str | None = None
        for attempt in range(policy.max_attempts):
            try:
                response = self._session.get(
                    url,
                    params=params,
                    headers=self._headers,
                    timeout=policy.timeout_seconds,
                )
            except policy.retryable_exceptions as exc:
                last_exception_name = type(exc).__name__
                self._sleep(min(2.0**attempt, policy.backoff_cap_seconds))
                continue
            status = response.status_code
            if status == 429:
                last_status = status
                delay = self._retry_after_seconds(response)
                if delay is None:
                    delay = 2.0**attempt
                self._sleep(min(delay, policy.retry_after_cap_seconds))
                continue
            if status >= 500:
                last_status = status
                self._sleep(min(2.0**attempt, policy.backoff_cap_seconds))
                continue
            if status >= 400:
                raise HttpRequestRefused(
                    f"HTTP {status} for {redacted_url(url)} -- not retryable"
                )
            return response
        detail = (
            f"last status {last_status}"
            if last_status is not None
            else f"last exception {last_exception_name}"
        )
        raise HttpRetryExhausted(
            f"HTTP GET failed after {policy.max_attempts} attempt(s) "
            f"({detail}): {redacted_url(url)} [query omitted]"
        )

    def _retry_after_seconds(self, response: Any) -> float | None:
        header = response.headers.get("Retry-After")
        if header is None:
            return None
        try:
            return float(header)
        except ValueError:
            pass
        try:
            retry_at = email.utils.parsedate_to_datetime(header)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - self._now()).total_seconds())
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_http.py -q`
Expected: all Task 1 tests PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean, full suite green.

---

### Task 2: `HttpClient.get_json` / `get_text` / `get_bytes`

**Files:**
- Modify: `src/wa_mine_monitor/http.py`
- Modify: `tests/test_http.py`

**Step 1: Write the failing tests** (append to `tests/test_http.py`)

```python
def test_get_json_returns_parsed_body():
    client, _, _ = _client([FakeResponse(200, json_body={"type": "Collection"})])
    assert client.get_json("https://example.test/c") == {"type": "Collection"}


def test_get_text_and_get_bytes():
    client, _, _ = _client(
        [FakeResponse(200, text="hello"), FakeResponse(200, text="hello")]
    )
    assert client.get_text("https://example.test/t") == "hello"
    assert client.get_bytes("https://example.test/t") == b"hello"


def test_convenience_methods_share_the_retry_loop():
    client, session, _ = _client(
        [FakeResponse(500), FakeResponse(200, json_body={"ok": 1})]
    )
    assert client.get_json("https://example.test/c") == {"ok": 1}
    assert len(session.calls) == 2
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_http.py -q -k "get_json or get_text or convenience"`
Expected: FAIL with `AttributeError: 'HttpClient' object has no attribute 'get_json'`

**Step 3: Write the implementation** (append methods to `HttpClient`)

```python
    def get_json(self, url: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return self.get(url, params=params).json()

    def get_text(self, url: str, *, params: Mapping[str, Any] | None = None) -> str:
        return self.get(url, params=params).text

    def get_bytes(self, url: str, *, params: Mapping[str, Any] | None = None) -> bytes:
        return self.get(url, params=params).content
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_http.py -q`
Expected: PASS.

---

### Task 3: `map_concurrent`

**Files:**
- Modify: `src/wa_mine_monitor/http.py`
- Modify: `tests/test_http.py`

**Step 1: Write the failing tests** (append to `tests/test_http.py`)

```python
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

    result = map_concurrent(
        maybe_fail, [0, 1, 2, 3], max_workers=2, tolerate_errors=True
    )
    assert result == [0, None, 2, None]


def test_tolerate_errors_defaults_to_false_so_errors_propagate():
    # `1 / 0` raises ZeroDivisionError, not ValueError -- assert the type the
    # callable actually raises so this step can go green.
    with pytest.raises(ZeroDivisionError):
        map_concurrent(lambda x: 1 / 0, [1], max_workers=2)
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_http.py -q -k "map_concurrent or serial or worker or lowest or inline or tolerate"`
Expected: FAIL with `ImportError: cannot import name 'map_concurrent'`

**Step 3: Write the implementation** (append to `http.py`; add
`from collections.abc import Callable, Iterable, Mapping, Sequence` and
`from concurrent.futures import ThreadPoolExecutor` to the imports)

```python
def map_concurrent(
    fn: Callable[[Any], Any],
    items: Iterable[Any],
    *,
    max_workers: int,
    tolerate_errors: bool = False,
) -> list[Any]:
    """Apply ``fn`` to every item; results ALWAYS in input order.

    ``max_workers`` is a required keyword: the caller must pass the source's
    declared ``RetryPolicy.max_workers`` rather than a call-site invention.
    With ``max_workers=1`` (or <=1 items) execution is inline on the calling
    thread -- no pool, deterministic, trivially debuggable.

    Failure semantics: by default the LOWEST-INDEX exception is raised after
    all tasks complete (deterministic regardless of scheduling); with
    ``tolerate_errors=True`` each failure yields ``None`` in its slot. A
    completeness-sensitive caller (the catalogue fetch) must NOT pass
    ``tolerate_errors=True`` -- a None-padded catalogue is a silent partial.
    """
    materialised: Sequence[Any] = list(items)
    workers = max(1, int(max_workers))
    if workers == 1 or len(materialised) <= 1:
        results: list[Any] = []
        for item in materialised:
            try:
                results.append(fn(item))
            except Exception:
                if tolerate_errors:
                    results.append(None)
                else:
                    raise
        return results

    slots: list[Any] = [None] * len(materialised)
    first_error: tuple[int, BaseException] | None = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): i for i, item in enumerate(materialised)}
        for future, index in futures.items():
            try:
                slots[index] = future.result()
            except Exception as exc:
                if tolerate_errors:
                    slots[index] = None
                elif first_error is None or index < first_error[0]:
                    first_error = (index, exc)
    if first_error is not None:
        raise first_error[1]
    return slots
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_http.py -q`
Expected: PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 4: `source_catalogue.py` — frozen `SourceSpec` and `DEA_COLLECTIONS`

**Files:**
- Create: `src/wa_mine_monitor/source_catalogue.py`
- Modify: `src/wa_mine_monitor/licence.py`
- Create: `tests/test_source_catalogue.py`

**Step 1: Write the failing tests**

```python
# tests/test_source_catalogue.py
"""Pin tests for the declarative source catalogue (D13 Batch C task C2).

The four DEA collection names were VERIFIED LIVE on 2026-08-15 (jarrah repo
research note `dea-probe-geomedian-fc-percentile_2026-08-15.md`): the
`*_nbart_gm_cyear_3` family resolves HTTP 200 as EMPTY STUBS (global
unbounded extent, zero items), so a name test is load-bearing, not
decorative.
"""

import re

import pytest

from wa_mine_monitor import licence
from wa_mine_monitor.source_catalogue import (
    DEA_COLLECTIONS,
    EMPTY_STUB_COLLECTION_PATTERN,
    SourceSpec,
    spec_for_collection,
    spec_for_source,
)

EXPECTED_COLLECTION_IDS = (
    "ga_ls5t_gm_cyear_3",
    "ga_ls7e_gm_cyear_3",
    "ga_ls8cls9c_gm_cyear_3",
    "ga_ls_fc_pc_cyear_3",
)


def test_exactly_the_four_verified_collections_are_pinned():
    assert tuple(s.collection_id for s in DEA_COLLECTIONS) == EXPECTED_COLLECTION_IDS


def test_no_pinned_collection_matches_the_empty_stub_naming_pattern():
    for spec in DEA_COLLECTIONS:
        assert not re.search(EMPTY_STUB_COLLECTION_PATTERN, spec.collection_id)


def test_the_stub_pattern_itself_catches_a_known_stub_name():
    # Positive control: a pattern that matches nothing guards nothing.
    assert re.search(EMPTY_STUB_COLLECTION_PATTERN, "ga_ls5t_nbart_gm_cyear_3")


def test_every_spec_source_id_exists_in_licence_sources_with_matching_licence():
    for spec in DEA_COLLECTIONS:
        record = licence.SOURCES[spec.source_id]
        assert spec.licence_state == record.licence_id
        assert spec.collection_id in record.source_url


def test_spec_is_frozen():
    spec = DEA_COLLECTIONS[0]
    with pytest.raises(AttributeError):
        spec.collection_id = "something-else"  # type: ignore[misc]


def test_spec_for_collection_round_trips_and_refuses_unknown():
    assert spec_for_collection("ga_ls_fc_pc_cyear_3").source_id == "dea_fc_pc"
    with pytest.raises(KeyError):
        spec_for_collection("ga_ls5t_nbart_gm_cyear_3")


def test_spec_for_source_round_trips_and_refuses_unknown():
    # The reverse lookup the coverage index needs: it keys items by
    # `source_id` but D13 C3 requires the COLLECTION identity in the frame.
    assert spec_for_source("dea_fc_pc").collection_id == "ga_ls_fc_pc_cyear_3"
    with pytest.raises(KeyError):
        spec_for_source("not_a_source")


def test_required_assets_cover_the_metrics_the_project_computes():
    gm = spec_for_collection("ga_ls5t_gm_cyear_3").asset_roles
    # NBR needs nir+swir_2, NDMI needs nir+swir_1; count is the support band.
    for asset in ("nbart_nir", "nbart_swir_1", "nbart_swir_2", "count"):
        assert asset in gm
    fc = spec_for_collection("ga_ls_fc_pc_cyear_3").asset_roles
    for asset in ("bs_pc_50", "pv_pc_50", "npv_pc_50", "qa"):
        assert asset in fc
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_source_catalogue.py -q`
Expected: `ModuleNotFoundError: No module named 'wa_mine_monitor.source_catalogue'`

**Step 3: Write the implementation**

```python
# src/wa_mine_monitor/source_catalogue.py
"""Declarative source specifications for remote collections.

Adapted from the dataplatform AdapterSpec/REGISTRY pattern under the D13 §3
reuse adjudication: ONLY the declarative frozen-spec contract is taken --
the dataplatform drivers are coupled to DuckLake/Polars/AWST and none of
that transfers. A spec here answers "which collection, what cadence, what
licence, which assets must every item carry" once, so fetch/validate code
reads the declaration instead of embedding the answers.

The four collection IDs were verified live 2026-08-15: the obvious
``*_nbart_gm_cyear_3`` names are EMPTY STUBS on the DEA Explorer STAC
(HTTP 200, global unbounded extent, zero items for any bbox) -- a pipeline
built on them passes existence checks and silently returns no data. Hence
``EMPTY_STUB_COLLECTION_PATTERN`` and its pin tests.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Regex identifying the retired stub family. Kept as a PATTERN (not a name
#: list) so `ga_ls9c_nbart_gm_cyear_3`-shaped future stubs are caught too.
EMPTY_STUB_COLLECTION_PATTERN = r"_nbart_gm_cyear"

#: Asset keys every geomedian item must carry (verified live from real item
#: `assets` dicts, 2026-08-15). The six reflectance bands make NBR, NDMI and
#: NDVI computable directly; `count` is the clear-observation support band.
GEOMEDIAN_REQUIRED_ASSETS: tuple[str, ...] = (
    "nbart_blue",
    "nbart_green",
    "nbart_red",
    "nbart_nir",
    "nbart_swir_1",
    "nbart_swir_2",
    "sdev",
    "edev",
    "bcdev",
    "count",
)

#: Asset keys every FC-percentile item must carry (verified live 2026-08-15).
FC_PC_REQUIRED_ASSETS: tuple[str, ...] = (
    "bs_pc_10",
    "bs_pc_50",
    "bs_pc_90",
    "pv_pc_10",
    "pv_pc_50",
    "pv_pc_90",
    "npv_pc_10",
    "npv_pc_50",
    "npv_pc_90",
    "qa",
)


@dataclass(frozen=True)
class SourceSpec:
    """One remote collection's frozen declaration."""

    source_id: str
    collection_id: str
    cadence: str
    region_scope: str
    licence_state: str
    asset_roles: tuple[str, ...]


DEA_COLLECTIONS: tuple[SourceSpec, ...] = (
    SourceSpec(
        source_id="dea_gm_ls5t",
        collection_id="ga_ls5t_gm_cyear_3",
        cadence="annual",
        region_scope="wa-statewide",
        licence_state="CC-BY-4.0",
        asset_roles=GEOMEDIAN_REQUIRED_ASSETS,
    ),
    SourceSpec(
        source_id="dea_gm_ls7e",
        collection_id="ga_ls7e_gm_cyear_3",
        cadence="annual",
        region_scope="wa-statewide",
        licence_state="CC-BY-4.0",
        asset_roles=GEOMEDIAN_REQUIRED_ASSETS,
    ),
    SourceSpec(
        source_id="dea_gm_ls8cls9c",
        collection_id="ga_ls8cls9c_gm_cyear_3",
        cadence="annual",
        region_scope="wa-statewide",
        licence_state="CC-BY-4.0",
        asset_roles=GEOMEDIAN_REQUIRED_ASSETS,
    ),
    SourceSpec(
        source_id="dea_fc_pc",
        collection_id="ga_ls_fc_pc_cyear_3",
        cadence="annual",
        region_scope="wa-statewide",
        licence_state="CC-BY-4.0",
        asset_roles=FC_PC_REQUIRED_ASSETS,
    ),
)

_BY_COLLECTION = {spec.collection_id: spec for spec in DEA_COLLECTIONS}
_BY_SOURCE = {spec.source_id: spec for spec in DEA_COLLECTIONS}


def spec_for_collection(collection_id: str) -> SourceSpec:
    """Return the pinned spec for ``collection_id``; KeyError on unknown."""
    return _BY_COLLECTION[collection_id]


def spec_for_source(source_id: str) -> SourceSpec:
    """Return the pinned spec for ``source_id``; KeyError on unknown.

    The coverage index keys captured items by ``source_id`` (the licence
    table's key) but D13 C3 requires the COLLECTION identity in the index
    frame; this is the one place the two identifiers are tied together.
    """
    return _BY_SOURCE[source_id]
```

Check the `source_id` keys against `licence.SOURCES` before running: the
existing keys are `dea_gm_ls5t`, `dea_gm_ls7e`, `dea_gm_ls8cls9c`,
`dea_fc_pc` (read them from `src/wa_mine_monitor/licence.py`, not from this
plan). If the geomedian spec's asset tuple fails the licence test because a
key differs, fix the SPEC, never the licence module.

The `licence.py` modification for this task is a lookup helper (append):

```python
def licence_for_collection(collection_id: str) -> SourceLicence:
    """Return the SourceLicence whose `source_url` pins `collection_id`.

    Used by the DEA catalogue fetch to compare a captured collection's own
    `license` field against the pinned record: the licence gate re-reads
    the licence from the captured JSON rather than trusting this table
    (D13 Batch C gate: "DEA licences must be re-read from the captured
    collection JSON").
    """
    for record in SOURCES.values():
        if record.source_url.endswith(f"/collections/{collection_id}"):
            return record
    raise KeyError(f"no pinned licence record for collection {collection_id!r}")
```

Add a test for it in `tests/test_source_catalogue.py`:

```python
def test_licence_for_collection_finds_all_four_and_refuses_unknown():
    for spec in DEA_COLLECTIONS:
        assert licence.licence_for_collection(spec.collection_id).licence_id == "CC-BY-4.0"
    with pytest.raises(KeyError):
        licence.licence_for_collection("ga_ls5t_nbart_gm_cyear_3")
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_source_catalogue.py -q`
Expected: PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 5: STAC fixtures and `sources/dea.py` validation

**Files:**
- Create: `tests/fixtures/dea/collection_ga_ls5t_gm_cyear_3.json` (and one per pinned collection)
- Create: `tests/fixtures/dea/collection_stub.json`
- Create: `tests/fixtures/dea/items_page_1.json`, `tests/fixtures/dea/items_page_2.json`
- Create: `src/wa_mine_monitor/sources/dea.py`
- Create: `tests/sources/test_dea.py`

**Step 1: Create the synthetic fixtures**

All fixtures are SYNTHETIC — hand-written minimal STAC shapes, never a saved
live response (a saved response embeds volatile fields and its licence status
is DEA's, not ours; a synthetic minimal shape is unambiguous test data).

`tests/fixtures/dea/collection_ga_ls5t_gm_cyear_3.json`:

```json
{
  "type": "Collection",
  "id": "ga_ls5t_gm_cyear_3",
  "stac_version": "1.0.0",
  "description": "Synthetic test fixture, not DEA data.",
  "license": "CC-BY-4.0",
  "extent": {
    "spatial": {"bbox": [[112.0, -36.0, 130.0, -13.0]]},
    "temporal": {"interval": [["1986-01-01T00:00:00Z", "2011-12-31T23:59:59Z"]]}
  },
  "links": []
}
```

Write the other three collection fixtures identically with the matching `id`
and a temporal interval per collection (`ga_ls7e_gm_cyear_3`: 1999–2021,
`ga_ls8cls9c_gm_cyear_3`: 2013–2025, `ga_ls_fc_pc_cyear_3`: 1987–2025).

`tests/fixtures/dea/collection_stub.json` (the empty-stub signature —
global unbounded extent, null temporal interval):

```json
{
  "type": "Collection",
  "id": "ga_ls5t_gm_cyear_3",
  "stac_version": "1.0.0",
  "description": "Synthetic stub-signature fixture.",
  "license": "CC-BY-4.0",
  "extent": {
    "spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]},
    "temporal": {"interval": [[null, null]]}
  },
  "links": []
}
```

`tests/fixtures/dea/items_page_1.json` — a FeatureCollection page with two
items and a `next` link (the geomedian asset set; `assets` values need only a
`href`):

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": "ga_ls5t_gm_cyear_3-x11y22-1990",
      "bbox": [116.0, -33.0, 117.0, -32.0],
      "properties": {
        "datetime": "1990-07-02T00:00:00Z",
        "odc:region_code": "x11y22",
        "odc:dataset_version": "4.0.0"
      },
      "assets": {
        "nbart_blue": {"href": "s3://x/b.tif"},
        "nbart_green": {"href": "s3://x/g.tif"},
        "nbart_red": {"href": "s3://x/r.tif"},
        "nbart_nir": {"href": "s3://x/n.tif"},
        "nbart_swir_1": {"href": "s3://x/s1.tif"},
        "nbart_swir_2": {"href": "s3://x/s2.tif"},
        "sdev": {"href": "s3://x/sd.tif"},
        "edev": {"href": "s3://x/ed.tif"},
        "bcdev": {"href": "s3://x/bc.tif"},
        "count": {"href": "s3://x/c.tif"}
      }
    },
    {
      "type": "Feature",
      "id": "ga_ls5t_gm_cyear_3-x11y23-1990",
      "bbox": [116.0, -34.0, 117.0, -33.0],
      "properties": {
        "datetime": "1990-07-02T00:00:00Z",
        "odc:region_code": "x11y23",
        "odc:dataset_version": "4.0.0"
      },
      "assets": {
        "nbart_blue": {"href": "s3://x/b.tif"},
        "nbart_green": {"href": "s3://x/g.tif"},
        "nbart_red": {"href": "s3://x/r.tif"},
        "nbart_nir": {"href": "s3://x/n.tif"},
        "nbart_swir_1": {"href": "s3://x/s1.tif"},
        "nbart_swir_2": {"href": "s3://x/s2.tif"},
        "sdev": {"href": "s3://x/sd.tif"},
        "edev": {"href": "s3://x/ed.tif"},
        "bcdev": {"href": "s3://x/bc.tif"},
        "count": {"href": "s3://x/c.tif"}
      }
    }
  ],
  "links": [
    {"rel": "next", "href": "https://example.test/stac/collections/ga_ls5t_gm_cyear_3/items?page=2"}
  ]
}
```

`tests/fixtures/dea/items_page_2.json` — one item
(`ga_ls5t_gm_cyear_3-x11y22-1991`, same shape, `datetime`
`"1991-07-02T00:00:00Z"`), `"links": []` (no `next`).

**Step 2: Write the failing tests**

```python
# tests/sources/test_dea.py
"""Tests for the DEA STAC catalogue source module (D13 Batch C task C2)."""

import json
from pathlib import Path

import pytest

from wa_mine_monitor.sources.dea import (
    CatalogueValidationError,
    validate_collection_json,
    validate_items,
)
from wa_mine_monitor.source_catalogue import spec_for_collection

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dea"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _spec():
    return spec_for_collection("ga_ls5t_gm_cyear_3")


def _items():
    return (
        _load("items_page_1.json")["features"]
        + _load("items_page_2.json")["features"]
    )


def test_valid_collection_json_passes_and_summarises():
    summary = validate_collection_json(
        _load("collection_ga_ls5t_gm_cyear_3.json"), _spec()
    )
    assert summary["collection_id"] == "ga_ls5t_gm_cyear_3"
    assert summary["license"] == "CC-BY-4.0"
    assert summary["temporal_extent"] == [
        "1986-01-01T00:00:00Z",
        "2011-12-31T23:59:59Z",
    ]
    # D13 C2 names the required assets among the recorded snapshot fields:
    # the summary carries the SPEC's declared asset roles, so a later reader
    # can see what "required assets" meant at capture time.
    assert summary["required_assets"] == list(_spec().asset_roles)


def test_stub_signature_is_rejected():
    with pytest.raises(CatalogueValidationError, match="stub"):
        validate_collection_json(_load("collection_stub.json"), _spec())


def test_wrong_collection_id_is_rejected():
    payload = _load("collection_ga_ls5t_gm_cyear_3.json")
    payload["id"] = "ga_ls7e_gm_cyear_3"
    with pytest.raises(CatalogueValidationError, match="id"):
        validate_collection_json(payload, _spec())


def test_licence_inconsistent_with_pinned_record_is_rejected():
    payload = _load("collection_ga_ls5t_gm_cyear_3.json")
    payload["license"] = "proprietary"
    with pytest.raises(CatalogueValidationError, match="licen"):
        validate_collection_json(payload, _spec())


def test_missing_temporal_extent_is_rejected():
    payload = _load("collection_ga_ls5t_gm_cyear_3.json")
    del payload["extent"]["temporal"]
    with pytest.raises(CatalogueValidationError, match="temporal"):
        validate_collection_json(payload, _spec())


def test_valid_items_pass_and_summarise():
    summary = validate_items(_items(), _spec())
    assert summary["n_items"] == 3
    assert summary["years"] == [1990, 1991]


def test_zero_items_are_rejected():
    with pytest.raises(CatalogueValidationError, match="0 item"):
        validate_items([], _spec())


def test_duplicate_item_ids_fail_reconciliation():
    items = _items()
    items.append(dict(items[0]))
    with pytest.raises(CatalogueValidationError, match="duplicate"):
        validate_items(items, _spec())


def test_item_missing_a_required_asset_is_rejected():
    items = _items()
    del items[0]["assets"]["nbart_swir_2"]
    with pytest.raises(CatalogueValidationError, match="nbart_swir_2"):
        validate_items(items, _spec())
```

**Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/sources/test_dea.py -q`
Expected: `ModuleNotFoundError: No module named 'wa_mine_monitor.sources.dea'`

**Step 4: Write the implementation**

```python
# src/wa_mine_monitor/sources/dea.py
"""DEA Explorer STAC catalogue: fetch, validate, and page collections.

Validation exists because collection EXISTENCE is not collection HEALTH:
the ``*_nbart_gm_cyear_3`` family answers HTTP 200 with a stub payload
(global unbounded bbox, null temporal interval, zero items). Every check
here rejects a specific measured failure shape, not a hypothetical.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from wa_mine_monitor.http import HttpClient, RetryPolicy, map_concurrent
from wa_mine_monitor.source_catalogue import SourceSpec

#: DEA Explorer STAC root. Pinned; a test asserts the exact value.
DEA_STAC_API_ROOT = "https://explorer.dea.ga.gov.au/stac"

#: WA statewide bbox -- same extent `sources/maus.py` clips to.
WA_BBOX: tuple[float, float, float, float] = (112.5, -35.5, 129.1, -13.5)

#: The stub signature measured live 2026-08-15.
_STUB_BBOX = [-180.0, -90.0, 180.0, 90.0]

#: Source-level transport policy for the DEA Explorer (public, unauthed,
#: modest fan-out: one worker per collection).
DEA_RETRY_POLICY = RetryPolicy(max_workers=4)

_USER_AGENT = "wa-mine-rehab-monitor/0.1 (github.com/jazzdos/wa-mine-rehab-monitor)"

#: Items per page requested from the paged items endpoint.
PAGE_LIMIT = 200

#: Hard page ceiling per collection -- a runaway `next`-link loop must fail
#: loudly, not fetch forever.
MAX_PAGES = 500


class CatalogueValidationError(Exception):
    """A captured collection or item set failed a health check."""


def collection_url(collection_id: str) -> str:
    return f"{DEA_STAC_API_ROOT}/collections/{collection_id}"


def items_url(collection_id: str) -> str:
    return f"{DEA_STAC_API_ROOT}/collections/{collection_id}/items"


def new_dea_client() -> HttpClient:
    return HttpClient(DEA_RETRY_POLICY, headers={"User-Agent": _USER_AGENT})


def validate_collection_json(payload: Mapping[str, Any], spec: SourceSpec) -> dict[str, Any]:
    """Validate a captured collection JSON against its pinned spec.

    Returns a summary dict for the snapshot's catalogue summary. Raises
    CatalogueValidationError naming the first failed check.
    """
    got_id = payload.get("id")
    if got_id != spec.collection_id:
        raise CatalogueValidationError(
            f"collection id mismatch: expected {spec.collection_id!r}, got {got_id!r}"
        )
    extent = payload.get("extent") or {}
    spatial = ((extent.get("spatial") or {}).get("bbox") or [None])[0]
    temporal = ((extent.get("temporal") or {}).get("interval") or [None])[0]
    if temporal is None or all(bound is None for bound in temporal):
        raise CatalogueValidationError(
            f"{spec.collection_id}: temporal extent absent or null -- the "
            f"empty-stub signature; a stub answers HTTP 200 and carries no data"
        )
    if spatial == _STUB_BBOX:
        raise CatalogueValidationError(
            f"{spec.collection_id}: global unbounded spatial extent -- the "
            f"empty-stub signature; a stub answers HTTP 200 and carries no data"
        )
    got_licence = payload.get("license")
    if got_licence != spec.licence_state:
        raise CatalogueValidationError(
            f"{spec.collection_id}: captured licence {got_licence!r} does not "
            f"match the pinned licence record {spec.licence_state!r} -- "
            f"re-adjudicate before any fetch proceeds"
        )
    return {
        "collection_id": spec.collection_id,
        "stac_url": collection_url(spec.collection_id),
        "license": got_licence,
        "temporal_extent": list(temporal),
        "spatial_extent": list(spatial) if spatial else None,
        # D13 C2 records the required assets alongside the collection: the
        # spec's declaration at capture time, so a later reader can tell
        # which asset set the fetch was validating against.
        "required_assets": list(spec.asset_roles),
    }


def validate_items(items: Sequence[Mapping[str, Any]], spec: SourceSpec) -> dict[str, Any]:
    """Validate a collection's full fetched item set.

    Zero items is a refusal (a stub or a wrong bbox, never a healthy
    catalogue); duplicate IDs fail reconciliation rather than inflating
    coverage; every item must carry the spec's required asset keys.
    """
    if not items:
        raise CatalogueValidationError(
            f"{spec.collection_id}: 0 items fetched -- an existing-but-empty "
            f"collection is the stub failure shape, not a healthy catalogue"
        )
    seen: set[str] = set()
    duplicates: list[str] = []
    years: set[int] = set()
    for item in items:
        item_id = str(item.get("id"))
        if item_id in seen:
            duplicates.append(item_id)
        seen.add(item_id)
        assets = item.get("assets") or {}
        missing = [role for role in spec.asset_roles if role not in assets]
        if missing:
            raise CatalogueValidationError(
                f"{spec.collection_id}: item {item_id} missing required "
                f"asset(s) {missing}"
            )
        stamp = (item.get("properties") or {}).get("datetime") or ""
        years.add(int(str(stamp)[:4]))
    if duplicates:
        raise CatalogueValidationError(
            f"{spec.collection_id}: {len(duplicates)} duplicate item id(s) "
            f"across pages (first: {duplicates[0]}) -- duplicates inflate "
            f"epoch coverage, so the fetch refuses rather than deduplicating"
        )
    return {
        "collection_id": spec.collection_id,
        "n_items": len(items),
        "years": sorted(years),
    }
```

**Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/sources/test_dea.py -q`
Expected: PASS.

**Step 6: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 6: `sources/dea.py` — paged fetch

**Files:**
- Modify: `src/wa_mine_monitor/sources/dea.py`
- Modify: `tests/sources/test_dea.py`

**Step 1: Write the failing tests** (append to `tests/sources/test_dea.py`)

```python
from wa_mine_monitor.sources.dea import (
    MAX_PAGES,
    fetch_collection_catalogue,
)


class FakeStacClient:
    """Maps URL -> payload; unknown URL raises. Records requested URLs."""

    def __init__(self, pages: dict):
        self._pages = pages
        self.requested: list[str] = []

    def get_json(self, url, *, params=None):
        self.requested.append(url)
        if url not in self._pages:
            raise AssertionError(f"unexpected URL {url}")
        payload = self._pages[url]
        if isinstance(payload, BaseException):
            raise payload
        return payload


def _fake_client_for_ls5t():
    from wa_mine_monitor.sources.dea import collection_url, items_url

    cid = "ga_ls5t_gm_cyear_3"
    return FakeStacClient(
        {
            collection_url(cid): _load("collection_ga_ls5t_gm_cyear_3.json"),
            items_url(cid): _load("items_page_1.json"),
            "https://example.test/stac/collections/ga_ls5t_gm_cyear_3/items?page=2": _load(
                "items_page_2.json"
            ),
        }
    )


def test_fetch_follows_next_links_and_returns_all_pages():
    client = _fake_client_for_ls5t()
    collection, pages, summary = fetch_collection_catalogue(client, _spec())
    assert collection["id"] == "ga_ls5t_gm_cyear_3"
    assert len(pages) == 2
    assert summary["n_items"] == 3
    assert summary["n_pages"] == 2


def test_reported_item_count_is_the_sources_own_numberMatched():
    """D13 C2 records the SOURCE's reported item count. When the API reports
    one it is captured verbatim -- never the fetched count relabelled."""
    from wa_mine_monitor.sources.dea import items_url

    client = _fake_client_for_ls5t()
    cid = "ga_ls5t_gm_cyear_3"
    client._pages[items_url(cid)] = {
        **client._pages[items_url(cid)],
        "numberMatched": 3,
    }
    _, _, summary = fetch_collection_catalogue(client, _spec())
    assert summary["reported_item_count"] == 3
    assert summary["reported_item_count_disclosure"] == "reported-by-source"


def test_absent_numberMatched_is_null_with_a_disclosure_not_the_fetched_count():
    client = _fake_client_for_ls5t()
    _, _, summary = fetch_collection_catalogue(client, _spec())
    assert summary["reported_item_count"] is None
    assert summary["reported_item_count_disclosure"] == "absent-from-source"
    # The fetched count is a SEPARATE field; the two must never be conflated.
    assert summary["n_items"] == 3


def test_fetch_refuses_a_stub_before_paging_items():
    from wa_mine_monitor.sources.dea import collection_url

    cid = "ga_ls5t_gm_cyear_3"
    client = FakeStacClient({collection_url(cid): _load("collection_stub.json")})
    with pytest.raises(CatalogueValidationError, match="stub"):
        fetch_collection_catalogue(client, _spec())
    # No items request was made -- the stub was refused at the collection.
    assert client.requested == [collection_url(cid)]


def test_fetch_refuses_a_next_link_loop_at_max_pages():
    from wa_mine_monitor.sources.dea import collection_url, items_url

    cid = "ga_ls5t_gm_cyear_3"
    looping_page = _load("items_page_1.json")
    looping_page["links"] = [{"rel": "next", "href": items_url(cid)}]
    client = FakeStacClient(
        {
            collection_url(cid): _load("collection_ga_ls5t_gm_cyear_3.json"),
            items_url(cid): looping_page,
        }
    )
    with pytest.raises(CatalogueValidationError, match=str(MAX_PAGES)):
        fetch_collection_catalogue(client, _spec())


def test_a_failing_page_propagates_no_partial_catalogue():
    """The completeness-sensitive-caller test D13 lists under C1: the
    catalogue fetch never tolerates a missing page -- a None-padded partial
    catalogue would silently understate coverage."""
    from wa_mine_monitor.sources.dea import collection_url, items_url

    cid = "ga_ls5t_gm_cyear_3"
    client = FakeStacClient(
        {
            collection_url(cid): _load("collection_ga_ls5t_gm_cyear_3.json"),
            items_url(cid): RuntimeError("transport died"),
        }
    )
    with pytest.raises(RuntimeError, match="transport died"):
        fetch_collection_catalogue(client, _spec())
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/sources/test_dea.py -q -k "fetch or failing_page"`
Expected: FAIL with `ImportError: cannot import name 'fetch_collection_catalogue'`

**Step 3: Write the implementation** (append to `sources/dea.py`)

```python
def _next_link(page: Mapping[str, Any]) -> str | None:
    for link in page.get("links") or []:
        if link.get("rel") == "next" and link.get("href"):
            return str(link["href"])
    return None


def fetch_collection_catalogue(
    client: Any, spec: SourceSpec
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Fetch one collection's JSON and every item page; validate both.

    Returns ``(collection_json, pages, summary)``. The collection is
    validated BEFORE any item request -- a stub is refused at one request,
    not after paging nothing. Pagination follows ``next`` links to a hard
    ``MAX_PAGES`` ceiling; item fetching is serial by construction (each
    page names the next), so concurrency lives at the ACROSS-collections
    level only. This caller is completeness-sensitive: it never passes
    ``tolerate_errors=True`` anywhere, and any page failure propagates.
    """
    collection = dict(client.get_json(collection_url(spec.collection_id)))
    collection_summary = validate_collection_json(collection, spec)

    pages: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    reported_item_count: int | None = None
    url: str | None = items_url(spec.collection_id)
    params: dict[str, Any] | None = {"limit": PAGE_LIMIT, "bbox": ",".join(map(str, WA_BBOX))}
    while url is not None:
        if len(pages) >= MAX_PAGES:
            raise CatalogueValidationError(
                f"{spec.collection_id}: exceeded {MAX_PAGES} item pages -- "
                f"refusing a possible next-link loop"
            )
        page = dict(client.get_json(url, params=params))
        params = None  # next links carry their own query
        pages.append(page)
        if reported_item_count is None and page.get("numberMatched") is not None:
            reported_item_count = int(page["numberMatched"])
        items.extend(page.get("features") or [])
        url = _next_link(page)

    item_summary = validate_items(items, spec)
    summary = {
        **collection_summary,
        **item_summary,
        "n_pages": len(pages),
        # D13 C2's "reported item count" is the SOURCE's own figure. The DEA
        # Explorer does not always emit `numberMatched`; when it does not,
        # this is null WITH a disclosure rather than the fetched count wearing
        # the source's label -- a count we produced is not a count the source
        # reported, and only the second can corroborate the first.
        "reported_item_count": reported_item_count,
        "reported_item_count_disclosure": (
            "reported-by-source" if reported_item_count is not None else "absent-from-source"
        ),
    }
    return collection, pages, summary
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/sources/test_dea.py -q`
Expected: PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 7: CLI `fetch-dea-catalogue`

**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `tests/test_cli.py`

One snapshot for the whole catalogue: `<data_root>/raw/dea_stac/<date>/`
containing `<collection_id>/collection.json`, `<collection_id>/items_page_NNNN.json`,
and `catalogue_summary.json` at the root. One run manifest (output =
`SHA256SUMS.txt`) with FOUR `SourceAsset` inputs — one per collection, each
carrying its own pinned licence fields. This gives C4/C5 a single
`source_catalogue_manifest` to chain to.

**Step 1: Write the failing tests** (append to `tests/test_cli.py`)

```python
def _dea_fixture_pages():
    fixtures = Path(__file__).resolve().parent / "fixtures" / "dea"

    def load(name):
        return json.loads((fixtures / name).read_text(encoding="utf-8"))

    from wa_mine_monitor.source_catalogue import DEA_COLLECTIONS
    from wa_mine_monitor.sources.dea import collection_url, items_url

    pages = {}
    for spec in DEA_COLLECTIONS:
        collection = load("collection_ga_ls5t_gm_cyear_3.json")
        collection["id"] = spec.collection_id
        page = load("items_page_2.json")  # single page, no next link
        for feature in page["features"]:
            feature["id"] = f"{spec.collection_id}-x11y22-1991"
            if spec.source_id == "dea_fc_pc":
                feature["assets"] = {
                    role: {"href": "s3://x/a.tif"} for role in spec.asset_roles
                }
        pages[collection_url(spec.collection_id)] = collection
        pages[items_url(spec.collection_id)] = page
    return pages


class _FakeCatalogueClient:
    def __init__(self, pages):
        self._pages = pages

    def get_json(self, url, *, params=None):
        payload = self._pages[url]
        if isinstance(payload, BaseException):
            raise payload
        return payload


def _write_monitor_config(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{tmp_path / "data"}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def test_fetch_dea_catalogue_writes_snapshot_and_manifest(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    fake = _FakeCatalogueClient(_dea_fixture_pages())
    monkeypatch.setattr("wa_mine_monitor.cli.new_dea_client", lambda: fake)

    result = runner.invoke(
        app,
        ["fetch-dea-catalogue", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert result.exit_code == 0, result.output
    snapshot_dir = tmp_path / "data" / "raw" / "dea_stac" / "2026-08-16"
    assert (snapshot_dir / "ga_ls5t_gm_cyear_3" / "collection.json").exists()
    assert (snapshot_dir / "ga_ls5t_gm_cyear_3" / "items_page_0001.json").exists()
    assert (snapshot_dir / "catalogue_summary.json").exists()
    assert (snapshot_dir / "SHA256SUMS.txt").exists()
    manifest_path = snapshot_dir / "SHA256SUMS.txt.run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["inputs"]) == 4
    payload = json.loads(result.output)
    assert payload["verify"] == {"ok": payload["verify"]["ok"], "bad": 0, "missing": 0}
    summary = json.loads(
        (snapshot_dir / "catalogue_summary.json").read_text(encoding="utf-8")
    )
    for entry in summary["collections"]:
        assert entry["n_items"] > 0
        # D13 C2's recorded snapshot fields, all three present per collection:
        assert entry["required_assets"]
        assert entry["reported_item_count_disclosure"] in {
            "reported-by-source",
            "absent-from-source",
        }
        assert len(entry["collection_response_sha256"]) == 64


def test_fetch_dea_catalogue_refuses_when_one_collection_fails(tmp_path, monkeypatch):
    """One collection failing refuses the WHOLE catalogue -- no partial,
    no finalized snapshot (the completeness-sensitive caller, end to end)."""
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    pages = _dea_fixture_pages()
    from wa_mine_monitor.sources.dea import collection_url

    pages[collection_url("ga_ls_fc_pc_cyear_3")] = RuntimeError("transport died")
    fake = _FakeCatalogueClient(pages)
    monkeypatch.setattr("wa_mine_monitor.cli.new_dea_client", lambda: fake)

    result = runner.invoke(
        app,
        ["fetch-dea-catalogue", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert result.exit_code == 1
    assert "refusal" in result.output
    snapshot_dir = tmp_path / "data" / "raw" / "dea_stac" / "2026-08-16"
    assert not (snapshot_dir / "SHA256SUMS.txt").exists()


def test_fetch_dea_catalogue_refuses_overwrite_of_finalized_snapshot(
    tmp_path, monkeypatch
):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    fake = _FakeCatalogueClient(_dea_fixture_pages())
    monkeypatch.setattr("wa_mine_monitor.cli.new_dea_client", lambda: fake)
    first = runner.invoke(
        app,
        ["fetch-dea-catalogue", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        app,
        ["fetch-dea-catalogue", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert second.exit_code == 1
    assert "refusal" in second.output
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -k "dea_catalogue"`
Expected: FAIL at `monkeypatch.setattr("wa_mine_monitor.cli.new_dea_client",
...)` with `AttributeError: <module 'wa_mine_monitor.cli'> has no attribute
'new_dea_client'` — the monkeypatch runs BEFORE the command is invoked, so
the tests never reach Typer's unknown-command exit code 2.

**Step 3: Write the implementation** (append to `cli.py`; follow
`fetch_tenements`'s lifecycle exactly — same helper calls, same structured
refusal shapes; new imports: `from wa_mine_monitor.source_catalogue import
DEA_COLLECTIONS`, `from wa_mine_monitor.sources.dea import
CatalogueValidationError, collection_url, fetch_collection_catalogue,
new_dea_client`, `from wa_mine_monitor.http import map_concurrent` and the
dea module's `DEA_RETRY_POLICY`)

```python
@app.command("fetch-dea-catalogue")
def fetch_dea_catalogue(
    config: Path = ConfigOption,
    date: str = DateOption,
) -> None:
    """Capture the four pinned DEA STAC collections into one dated snapshot.

    Fetches collection JSON + every WA-bbox item page for each collection in
    `source_catalogue.DEA_COLLECTIONS`, validates health (stub signature,
    zero/duplicate items, licence consistency, required assets), and writes
    an immutable snapshot at `<data_root>/raw/dea_stac/<date>/` with one run
    manifest carrying four SourceAsset inputs. Any single collection failing
    refuses the WHOLE run before finalization -- a partial catalogue would
    silently understate coverage downstream (C3/C5).
    """
    resolved = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    snapshot_dir = snapshots.create_snapshot_dir(resolved.run.data_root, "dea_stac", date)
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    _refuse_if_snapshot_already_finalized(snapshot_dir, config=resolved_config, git_state=git_state)

    client = new_dea_client()

    def fetch_one(spec):  # noqa: ANN001, ANN202 -- SourceSpec -> tuple
        return spec, fetch_collection_catalogue(client, spec)

    try:
        fetched = map_concurrent(
            fetch_one, DEA_COLLECTIONS, max_workers=DEA_RETRY_POLICY.max_workers
        )
    except CatalogueValidationError as exc:
        typer.echo(
            json.dumps(
                {"refusal": f"DEA catalogue validation failed: {exc}", "stage": "validation"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    except Exception as exc:  # noqa: BLE001 -- surfaced as a structured refusal
        typer.echo(
            json.dumps(
                {"refusal": f"DEA catalogue fetch failed: {exc}", "stage": "download"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    collection_summaries = []
    collection_digests: dict[str, str] = {}
    for spec, (collection, pages, summary) in fetched:
        subdir = snapshot_dir / spec.collection_id
        subdir.mkdir(parents=True, exist_ok=True)
        collection_path = subdir / "collection.json"
        collection_path.write_text(
            json.dumps(collection, indent=2, sort_keys=True), encoding="utf-8"
        )
        for page_number, page in enumerate(pages, start=1):
            (subdir / f"items_page_{page_number:04d}.json").write_text(
                json.dumps(page, indent=2, sort_keys=True), encoding="utf-8"
            )
        # D13 C2's "response digest": the digest of the CAPTURED
        # collection.json bytes as they landed on disk, so the summary and
        # the snapshot's own SHA256SUMS entry describe the same bytes.
        collection_digests[spec.source_id] = sha256_file(collection_path)
        collection_summaries.append(
            {
                **summary,
                "source_id": spec.source_id,
                "fetch_date": date,
                "collection_response_sha256": collection_digests[spec.source_id],
            }
        )

    (snapshot_dir / "catalogue_summary.json").write_text(
        json.dumps({"collections": collection_summaries}, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    licence_notes = "; ".join(
        f"{spec.collection_id}: {licence.SOURCES[spec.source_id].licence_id}"
        for spec in DEA_COLLECTIONS
    )
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="DEA Explorer STAC (four pinned annual collections)",
        endpoint=collection_url(DEA_COLLECTIONS[0].collection_id).rsplit("/", 2)[0],
        licence_note=f"Licences re-read from each captured collection.json: {licence_notes}",
        purpose=(
            "Pinned DEA STAC catalogue snapshot for the WA mine rehabilitation "
            "spectral monitor's epoch-coverage index and volume estimate."
        ),
    )
    sums_path = snapshots.finalize_snapshot(snapshot_dir)
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)

    input_assets = [
        SourceAsset(
            uri=collection_url(spec.collection_id),
            sha256=collection_digests[spec.source_id],
            collection=spec.collection_id,
            snapshot_date=dt_date.fromisoformat(date),
            licence=licence.SOURCES[spec.source_id].licence_id,
            redistribute_public=licence.SOURCES[spec.source_id].redistribute_public,
        )
        for spec in DEA_COLLECTIONS
    ]
    manifests.write_run_manifest(
        output=sums_path,
        inputs=input_assets,
        config=resolved_config,
        git_state=git_state,
        resolved_args={"date": date, "collections": collection_summaries},
    )

    typer.echo(
        json.dumps(
            {
                "snapshot_dir": str(snapshot_dir),
                "verify": {"ok": n_ok, "bad": n_bad, "missing": n_missing},
                "collections": collection_summaries,
                "manifest_path": str(sums_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
```

Note for the implementer: `map_concurrent` over four collections shares one
`HttpClient`; `requests.Session` is not formally thread-safe, but the fake in
tests is, and the LIVE run may simply construct the client per call inside
`fetch_one` via `new_dea_client()` if review prefers — keep the monkeypatch
seam (`cli.new_dea_client`) either way.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 8: `dea_coverage.py` — `build_item_index`

**Files:**
- Create: `src/wa_mine_monitor/dea_coverage.py`
- Create: `tests/test_dea_coverage.py`

**Step 1: Write the failing tests**

```python
# tests/test_dea_coverage.py
"""Tests for the DEA epoch-coverage index (D13 Batch C task C3)."""

import pandas as pd
import pytest

from wa_mine_monitor.dea_coverage import (
    ITEM_INDEX_COLUMNS,
    build_item_index,
)


def _item(item_id, *, bbox, year, tile="x11y22", version="4.0.0", assets=None):
    return {
        "id": item_id,
        "bbox": list(bbox),
        "properties": {
            "datetime": f"{year}-07-02T00:00:00Z",
            "odc:region_code": tile,
            "odc:dataset_version": version,
        },
        "assets": assets
        if assets is not None
        else {"nbart_nir": {"href": "s3://x/n.tif"}, "count": {"href": "s3://x/c.tif"}},
    }


BBOX_A = (116.0, -33.0, 117.0, -32.0)
BBOX_B = (117.0, -33.0, 118.0, -32.0)


def test_build_item_index_columns_and_content():
    items = {"dea_gm_ls5t": [_item("a-1990", bbox=BBOX_A, year=1990)]}
    index, duplicates = build_item_index(items)
    assert tuple(index.columns) == ITEM_INDEX_COLUMNS
    row = index.iloc[0]
    assert row["source_id"] == "dea_gm_ls5t"
    # D13 C3 names COLLECTION identity and ASSET identity as index fields;
    # source_id alone renames the first and drops the second.
    assert row["collection_id"] == "ga_ls5t_gm_cyear_3"
    assert row["asset_identity"] == "count|nbart_nir"
    assert row["item_id"] == "a-1990"
    assert row["year"] == 1990
    assert row["tile_id"] == "x11y22"
    assert row["product_version"] == "4.0.0"
    assert (row["bbox_west"], row["bbox_north"]) == (116.0, -32.0)
    assert duplicates == {"dea_gm_ls5t": 0}


def test_asset_identity_distinguishes_items_with_different_asset_sets():
    items = {
        "dea_gm_ls5t": [
            _item("a-1990", bbox=BBOX_A, year=1990),
            _item(
                "b-1990",
                bbox=BBOX_B,
                year=1990,
                assets={"count": {"href": "s3://x/c.tif"}},
            ),
        ]
    }
    index, _ = build_item_index(items)
    assert sorted(index["asset_identity"]) == ["count", "count|nbart_nir"]


def test_unknown_source_id_is_refused_not_silently_indexed():
    with pytest.raises(KeyError):
        build_item_index({"not_a_pinned_source": [_item("a", bbox=BBOX_A, year=1990)]})


def test_item_without_assets_is_a_refusal():
    broken = _item("a-1990", bbox=BBOX_A, year=1990)
    broken["assets"] = {}
    with pytest.raises(ValueError, match="asset"):
        build_item_index({"dea_gm_ls5t": [broken]})


def test_duplicate_item_ids_are_refused_with_a_count_not_double_counted():
    items = {
        "dea_gm_ls5t": [
            _item("a-1990", bbox=BBOX_A, year=1990),
            _item("a-1990", bbox=BBOX_A, year=1990),
            _item("b-1990", bbox=BBOX_B, year=1990, tile="x12y22"),
        ]
    }
    index, duplicates = build_item_index(items)
    assert len(index) == 2
    assert duplicates == {"dea_gm_ls5t": 1}


def test_sources_stay_separate():
    items = {
        "dea_gm_ls5t": [_item("a-1990", bbox=BBOX_A, year=1990)],
        "dea_gm_ls7e": [_item("a-1990", bbox=BBOX_A, year=1990)],
    }
    index, _ = build_item_index(items)
    assert sorted(index["source_id"]) == ["dea_gm_ls5t", "dea_gm_ls7e"]


def test_item_without_bbox_is_a_refusal_not_a_skip():
    broken = _item("a-1990", bbox=BBOX_A, year=1990)
    del broken["bbox"]
    with pytest.raises(ValueError, match="bbox"):
        build_item_index({"dea_gm_ls5t": [broken]})
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dea_coverage.py -q`
Expected: `ModuleNotFoundError: No module named 'wa_mine_monitor.dea_coverage'`

**Step 3: Write the implementation**

```python
# src/wa_mine_monitor/dea_coverage.py
"""Per-site DEA epoch coverage from a captured STAC catalogue snapshot.

An EPOCH is a distinct calendar year with at least one intersecting item in
a collection: multiple tiles covering one site in one year are ONE epoch
(the site sits on a tile boundary, not in two years). Coverage counts use
the register's internal MINEDEX point for the Tier 0 coverage DIAGNOSTIC
only -- a point-in-bbox test never defines or substitutes a Tier 1
footprint (D13 C3 acceptance).

Null vs zero follows the register's ``n_tenements_intersecting`` semantic
(D12.2): a coordinate-less site gets NULL (not computable), a located site
with no intersecting item gets a GENUINE ZERO.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from wa_mine_monitor.source_catalogue import spec_for_source

#: Column order of the item index frame. D13 C3 requires collection, item
#: ID, year, geometry, ASSET IDENTITY, product version and tile identity:
#: `source_id` is the licence table's key for a collection, not the
#: collection's own id, so both are carried.
ITEM_INDEX_COLUMNS: tuple[str, ...] = (
    "source_id",
    "collection_id",
    "item_id",
    "year",
    "bbox_west",
    "bbox_south",
    "bbox_east",
    "bbox_north",
    "tile_id",
    "product_version",
    "asset_identity",
)

#: source_id -> enriched-register column (D13 C3 field names, exact).
DEA_EPOCH_COLUMN_BY_SOURCE: dict[str, str] = {
    "dea_gm_ls5t": "n_dea_gm_ls5t_epochs",
    "dea_gm_ls7e": "n_dea_gm_ls7e_epochs",
    "dea_gm_ls8cls9c": "n_dea_gm_ls8cls9c_epochs",
    "dea_fc_pc": "n_dea_fc_pc_epochs",
}

#: Fixed keys of the per-collection coverage disclosure -- three counts plus
#: the item-reconciliation pair, never a boolean (the
#: `tenement_count_disclosure` discipline).
COVERAGE_DISCLOSURE_KEYS: tuple[str, ...] = (
    "n_sites_coverage_computed",
    "n_sites_coverage_zero",
    "n_sites_coverage_not_computed",
    "n_distinct_items",
    "n_duplicate_items_refused",
)


def build_item_index(
    items_by_source: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Flatten captured STAC items into one indexable frame.

    Returns ``(index, duplicates_refused)`` where ``duplicates_refused``
    counts, per source, items dropped because their id was already seen --
    reported (never silent) and carried into the coverage disclosure. C2's
    fetch already refuses duplicates at capture; this second count exists
    so an index built from any OTHER item source inherits the guard.
    """
    rows: list[dict[str, Any]] = []
    duplicates_refused: dict[str, int] = {}
    for source_id, items in items_by_source.items():
        # KeyError on an unpinned source: an index row whose collection
        # cannot be named is a coverage claim with no provenance.
        collection_id = spec_for_source(source_id).collection_id
        seen: set[str] = set()
        duplicates_refused[source_id] = 0
        for item in items:
            item_id = str(item.get("id"))
            if item_id in seen:
                duplicates_refused[source_id] += 1
                continue
            seen.add(item_id)
            bbox = item.get("bbox")
            if not bbox or len(bbox) != 4:
                raise ValueError(
                    f"{source_id}: item {item_id} has no usable bbox -- a "
                    f"skipped item is invisible coverage loss, so this refuses"
                )
            properties = item.get("properties") or {}
            stamp = str(properties.get("datetime") or "")
            if len(stamp) < 4 or not stamp[:4].isdigit():
                raise ValueError(
                    f"{source_id}: item {item_id} has no parseable datetime year"
                )
            assets = item.get("assets") or {}
            if not assets:
                raise ValueError(
                    f"{source_id}: item {item_id} carries no assets -- asset "
                    f"identity is a declared index field (D13 C3), so an "
                    f"assetless item is refused rather than indexed blank"
                )
            rows.append(
                {
                    "source_id": source_id,
                    "collection_id": collection_id,
                    "item_id": item_id,
                    "year": int(stamp[:4]),
                    "bbox_west": float(bbox[0]),
                    "bbox_south": float(bbox[1]),
                    "bbox_east": float(bbox[2]),
                    "bbox_north": float(bbox[3]),
                    "tile_id": str(properties.get("odc:region_code") or ""),
                    "product_version": str(properties.get("odc:dataset_version") or ""),
                    # Asset identity: the item's sorted asset keys, joined.
                    # Readable and directly comparable to a spec's
                    # `asset_roles`, so a mid-series asset-set change shows up
                    # as a value difference rather than an opaque digest.
                    "asset_identity": "|".join(sorted(assets)),
                }
            )
    index = pd.DataFrame(rows, columns=list(ITEM_INDEX_COLUMNS))
    return index, duplicates_refused
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dea_coverage.py -q`
Expected: PASS.

---

### Task 9: `dea_coverage.py` — `count_site_epochs` and disclosures

**Files:**
- Modify: `src/wa_mine_monitor/dea_coverage.py`
- Modify: `tests/test_dea_coverage.py`

**Step 1: Write the failing tests** (append to `tests/test_dea_coverage.py`)

```python
import pyarrow as pa

from wa_mine_monitor import tables
from wa_mine_monitor.dea_coverage import (
    COVERAGE_DISCLOSURE_KEYS,
    DEA_EPOCH_COLUMN_BY_SOURCE,
    count_site_epochs,
)


def _register(rows):
    return pd.DataFrame(rows, columns=["site_id", "lon", "lat"])


def _coverage_inputs(items_by_source):
    index, duplicates = build_item_index(items_by_source)
    return index, duplicates


def test_coordinate_less_site_gets_null_for_all_four_counts():
    register = _register([{"site_id": "S1", "lon": None, "lat": None}])
    index, dups = _coverage_inputs({"dea_gm_ls5t": [_item("a", bbox=BBOX_A, year=1990)]})
    coverage, disclosures = count_site_epochs(register, index, duplicates_refused=dups)
    row = coverage.set_index("site_id").loc["S1"]
    for column in DEA_EPOCH_COLUMN_BY_SOURCE.values():
        assert pd.isna(row[column])


def test_located_site_with_no_item_gets_genuine_zero():
    register = _register([{"site_id": "S1", "lon": 150.0, "lat": -20.0}])
    index, dups = _coverage_inputs({"dea_gm_ls5t": [_item("a", bbox=BBOX_A, year=1990)]})
    coverage, disclosures = count_site_epochs(register, index, duplicates_refused=dups)
    assert coverage.set_index("site_id").loc["S1", "n_dea_gm_ls5t_epochs"] == 0
    assert disclosures["dea_gm_ls5t"]["n_sites_coverage_zero"] == 1


def test_multiple_tiles_in_one_year_count_as_one_epoch():
    # Site at the shared corner of two tiles, both 1990: one epoch.
    register = _register([{"site_id": "S1", "lon": 117.0, "lat": -32.5}])
    index, dups = _coverage_inputs(
        {
            "dea_gm_ls5t": [
                _item("a-1990", bbox=BBOX_A, year=1990),
                _item("b-1990", bbox=BBOX_B, year=1990, tile="x12y22"),
                _item("a-1991", bbox=BBOX_A, year=1991),
            ]
        }
    )
    coverage, _ = count_site_epochs(register, index, duplicates_refused=dups)
    assert coverage.set_index("site_id").loc["S1", "n_dea_gm_ls5t_epochs"] == 2


def test_overlapping_sensor_collections_remain_separate():
    register = _register([{"site_id": "S1", "lon": 116.5, "lat": -32.5}])
    index, dups = _coverage_inputs(
        {
            "dea_gm_ls5t": [_item("a-1990", bbox=BBOX_A, year=1990)],
            "dea_gm_ls7e": [
                _item("c-1999", bbox=BBOX_A, year=1999),
                _item("c-2000", bbox=BBOX_A, year=2000),
            ],
        }
    )
    coverage, _ = count_site_epochs(register, index, duplicates_refused=dups)
    row = coverage.set_index("site_id").loc["S1"]
    assert row["n_dea_gm_ls5t_epochs"] == 1
    assert row["n_dea_gm_ls7e_epochs"] == 2


def test_disclosure_reconciles_to_register_rows_and_carries_fixed_keys():
    register = _register(
        [
            {"site_id": "S1", "lon": 116.5, "lat": -32.5},
            {"site_id": "S2", "lon": 150.0, "lat": -20.0},
            {"site_id": "S3", "lon": None, "lat": None},
        ]
    )
    index, dups = _coverage_inputs({"dea_gm_ls5t": [_item("a", bbox=BBOX_A, year=1990)]})
    _, disclosures = count_site_epochs(register, index, duplicates_refused=dups)
    disclosure = disclosures["dea_gm_ls5t"]
    assert tuple(disclosure.keys()) == COVERAGE_DISCLOSURE_KEYS
    assert (
        disclosure["n_sites_coverage_computed"]
        + disclosure["n_sites_coverage_not_computed"]
        == len(register)
    )
    assert disclosure["n_sites_coverage_computed"] == 2
    assert disclosure["n_sites_coverage_zero"] == 1
    assert disclosure["n_sites_coverage_not_computed"] == 1
    assert disclosure["n_distinct_items"] == 1
    assert disclosure["n_duplicate_items_refused"] == 0


def test_counts_survive_declared_arrow_write_read(tmp_path):
    register = _register(
        [
            {"site_id": "S1", "lon": 116.5, "lat": -32.5},
            {"site_id": "S2", "lon": None, "lat": None},
        ]
    )
    index, dups = _coverage_inputs({"dea_gm_ls5t": [_item("a", bbox=BBOX_A, year=1990)]})
    coverage, _ = count_site_epochs(register, index, duplicates_refused=dups)
    schema = pa.schema(
        [pa.field("site_id", pa.string())]
        + [
            pa.field(column, pa.int64(), nullable=True)
            for column in DEA_EPOCH_COLUMN_BY_SOURCE.values()
        ]
    )
    path = tmp_path / "coverage.parquet"
    tables.write_table(coverage, path, schema)
    read_back = tables.read_table(path)
    assert read_back["n_dea_gm_ls5t_epochs"].tolist()[0] == 1
    assert pd.isna(read_back["n_dea_gm_ls5t_epochs"].tolist()[1])
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dea_coverage.py -q -k "count_site or coordinate_less or genuine_zero or one_epoch or remain_separate or reconciles or arrow"`
Expected: FAIL with `ImportError: cannot import name 'count_site_epochs'`

**Step 3: Write the implementation** (append to `dea_coverage.py`)

```python
def count_site_epochs(
    register: pd.DataFrame,
    item_index: pd.DataFrame,
    *,
    duplicates_refused: Mapping[str, int],
) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """Count distinct intersecting item-years per site per collection.

    Returns ``(coverage, disclosures)``: coverage has ``site_id`` plus the
    four nullable Int64 epoch columns in `DEA_EPOCH_COLUMN_BY_SOURCE` order;
    disclosures maps source_id -> the fixed-key count dict. A source with
    no rows in ``item_index`` still gets a column (zeros for located sites)
    and a disclosure -- an unfetched collection must read as zero-item, not
    silently absent, so the reconciliation check can see it.
    """
    located = register["lon"].notna() & register["lat"].notna()
    coverage = pd.DataFrame({"site_id": register["site_id"].to_numpy()})
    disclosures: dict[str, dict[str, int]] = {}

    for source_id, column in DEA_EPOCH_COLUMN_BY_SOURCE.items():
        subset = item_index[item_index["source_id"] == source_id]
        counts = pd.array([pd.NA] * len(register), dtype="Int64")
        n_zero = 0
        for position in range(len(register)):
            if not bool(located.iloc[position]):
                continue
            lon = float(register["lon"].iloc[position])
            lat = float(register["lat"].iloc[position])
            if len(subset):
                hits = (
                    (subset["bbox_west"] <= lon)
                    & (lon <= subset["bbox_east"])
                    & (subset["bbox_south"] <= lat)
                    & (lat <= subset["bbox_north"])
                )
                n_epochs = int(subset.loc[hits, "year"].nunique())
            else:
                n_epochs = 0
            counts[position] = n_epochs
            if n_epochs == 0:
                n_zero += 1
        coverage[column] = counts
        disclosures[source_id] = {
            "n_sites_coverage_computed": int(located.sum()),
            "n_sites_coverage_zero": n_zero,
            "n_sites_coverage_not_computed": int((~located).sum()),
            "n_distinct_items": int(len(subset)),
            "n_duplicate_items_refused": int(duplicates_refused.get(source_id, 0)),
        }
    return coverage, disclosures
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dea_coverage.py -q`
Expected: PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 10: `register.py` — enriched schema and enrichment function

**Files:**
- Modify: `src/wa_mine_monitor/register.py`
- Modify: `tests/test_register.py`

**Step 1: Write the failing tests** (append to `tests/test_register.py`;
reuse that file's existing register-frame fixture helpers rather than
inventing new ones — read the file's helpers first and build a minimal
conforming frame the way its other tests do)

```python
def _coverage_frame(site_ids, values=1):
    import pandas as pd

    from wa_mine_monitor.dea_coverage import DEA_EPOCH_COLUMN_BY_SOURCE

    frame = pd.DataFrame({"site_id": list(site_ids)})
    for column in DEA_EPOCH_COLUMN_BY_SOURCE.values():
        frame[column] = pd.array([values] * len(site_ids), dtype="Int64")
    return frame


def test_enriched_schema_is_register_schema_plus_four_nullable_int64():
    from wa_mine_monitor.register import (
        DEA_COVERAGE_COLUMNS,
        ENRICHED_REGISTER_SCHEMA,
        REGISTER_SCHEMA,
    )

    assert ENRICHED_REGISTER_SCHEMA.names == REGISTER_SCHEMA.names + list(
        DEA_COVERAGE_COLUMNS
    )
    for column in DEA_COVERAGE_COLUMNS:
        field = ENRICHED_REGISTER_SCHEMA.field(column)
        assert field.type == pa.int64()
        assert field.nullable


def _conforming_register(n_rows: int) -> pd.DataFrame:
    """A conforming register frame from this file's OWN fixture builders --
    `_sites_df`/`_owners_df`/`_tenements_gdf` through `build_register`, the
    same construction every other test in this file uses."""
    rows = [
        _sites_row(site_code=f"M{i:04d}", lon=116.0 + i, lat=-32.0 - i)
        for i in range(n_rows)
    ]
    return build_register(_sites_df(rows), _owners_df([]), _tenements_gdf([]), "2026-08-15")


def test_enrich_appends_columns_preserving_row_identity_and_order():
    register_df = _conforming_register(3)
    coverage = _coverage_frame(register_df["site_id"])
    enriched = register_module.enrich_register_with_dea_coverage(register_df, coverage)
    assert list(enriched["site_id"]) == list(register_df["site_id"])
    assert list(enriched.columns[: len(register_df.columns)]) == list(register_df.columns)
    assert str(enriched["n_dea_fc_pc_epochs"].dtype) == "Int64"
    # Existing nullable semantics untouched:
    assert str(enriched["n_tenements_intersecting"].dtype) == "Int64"


def test_enrich_refuses_row_loss_gain_and_mismatched_sites():
    register_df = _conforming_register(2)
    with pytest.raises(register_module.RegisterEnrichmentError, match="site"):
        register_module.enrich_register_with_dea_coverage(
            register_df, _coverage_frame(["NOT-A-SITE", "ALSO-NOT"])
        )
    with pytest.raises(register_module.RegisterEnrichmentError, match="row"):
        register_module.enrich_register_with_dea_coverage(
            register_df, _coverage_frame(register_df["site_id"][:1])
        )
```

`_conforming_register` above uses this file's existing builders
(`_sites_row`/`_sites_df`/`_owners_df`/`_tenements_gdf` + `build_register`,
all already imported at the top of `tests/test_register.py`) and
`register_module`, the module alias that file already imports — no parallel
fixture, no new import beyond `pytest` (already present). `tests/test_register.py` does NOT currently
import `pyarrow`; add `import pyarrow as pa` for the schema assertions.

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_register.py -q -k "enrich"`
Expected: FAIL with `ImportError`/`AttributeError` on the new names.

**Step 3: Write the implementation** (append to `register.py`)

```python
from wa_mine_monitor.dea_coverage import DEA_EPOCH_COLUMN_BY_SOURCE

#: The four appended coverage columns, in declared order (D13 C3/C4).
DEA_COVERAGE_COLUMNS: tuple[str, ...] = tuple(DEA_EPOCH_COLUMN_BY_SOURCE.values())

#: REGISTER_SCHEMA plus the four nullable epoch counts -- built FROM the
#: base schema so the two can never drift.
ENRICHED_REGISTER_SCHEMA = pa.schema(
    list(REGISTER_SCHEMA)
    + [pa.field(column, pa.int64(), nullable=True) for column in DEA_COVERAGE_COLUMNS]
)


class RegisterEnrichmentError(ValueError):
    """Enrichment would change row identity, count or order -- refused."""


def enrich_register_with_dea_coverage(
    register_df: pd.DataFrame, coverage_df: pd.DataFrame
) -> pd.DataFrame:
    """Append the four epoch-coverage columns; NEVER touch existing rows.

    Refuses on any site-set or order difference: enrichment is an append of
    columns, and a merge that drops, adds or reorders rows is a different
    register wearing the old one's name (D13 C4: before/after row totals
    equal, order byte-stable apart from the appended fields).
    """
    if len(coverage_df) != len(register_df):
        raise RegisterEnrichmentError(
            f"coverage has {len(coverage_df)} row(s) against the register's "
            f"{len(register_df)} -- row loss or gain is refused"
        )
    register_sites = register_df["site_id"].tolist()
    coverage_sites = coverage_df["site_id"].tolist()
    if register_sites != coverage_sites:
        if sorted(register_sites) == sorted(coverage_sites):
            raise RegisterEnrichmentError(
                "coverage site_id ORDER differs from the register -- reordering is refused"
            )
        raise RegisterEnrichmentError(
            "coverage site_id set differs from the register -- mismatched sites refused"
        )
    enriched = register_df.copy()
    for column in DEA_COVERAGE_COLUMNS:
        enriched[column] = coverage_df[column].array
    return enriched
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_register.py -q`
Expected: PASS (all pre-existing register tests still green).

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 11: CLI `build-dea-coverage`

**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `tests/test_cli.py`

Command shape: `build-dea-coverage --config <path> --date <output date>
--catalogue-date <raw/dea_stac date>`. Pipeline: locate latest
`curated/register/<date>/` (via `_latest_curated_dated_dir`) → digest-verify
its `register.parquet` against its own manifest's `output.sha256` → verify
the catalogue snapshot (`_verify_snapshot_or_refuse` on
`raw/dea_stac/<catalogue-date>/`) → load item pages → `build_item_index` →
`count_site_epochs` → `enrich_register_with_dea_coverage` → write a NEW
`curated/register/<date>/register.parquet` under `ENRICHED_REGISTER_SCHEMA`
(refusing if that dated dir already has one) → manifest with the D13-named
`resolved_args` fields.

**Step 1: Write the failing tests** (append to `tests/test_cli.py`)

```python
def _seed_curated_register(tmp_path, cfg_file, monkeypatch):
    """Produce a real Batch B register via the existing build path, or
    construct a minimal conforming register.parquet + manifest directly with
    register/tables/manifests APIs -- follow whichever seam test_cli.py
    already uses for build-crosswalk tests."""
    ...


def test_build_dea_coverage_writes_new_versioned_register(tmp_path, monkeypatch):
    # Arrange: seeded curated register (date 2026-08-15) + fetched dea_stac
    # snapshot (date 2026-08-16, via the Task 7 fake client).
    ...
    result = runner.invoke(
        app,
        [
            "build-dea-coverage",
            "--config", str(cfg_file),
            "--date", "2026-08-17",
            "--catalogue-date", "2026-08-16",
        ],
    )
    assert result.exit_code == 0, result.output
    out_dir = tmp_path / "data" / "curated" / "register" / "2026-08-17"
    assert (out_dir / "register.parquet").exists()
    manifest = json.loads(
        (out_dir / "register.parquet.run_manifest.json").read_text(encoding="utf-8")
    )
    args = manifest["resolved_args"]
    assert set(args) >= {
        "source_register_manifest",
        "source_catalogue_manifest",
        "dea_coverage_disclosure",
        "minedex_public_export_blocked",
        "register_rows_before",
        "register_rows_after",
    }
    assert args["register_rows_before"] == args["register_rows_after"]
    assert args["minedex_public_export_blocked"] is True
    # Batch B artefact untouched:
    source_dir = tmp_path / "data" / "curated" / "register" / "2026-08-15"
    assert (source_dir / "register.parquet").exists()
    # Columns preserved + four appended, dtypes nullable:
    from wa_mine_monitor import tables
    from wa_mine_monitor.register import DEA_COVERAGE_COLUMNS, REGISTER_SCHEMA

    enriched = tables.read_table(out_dir / "register.parquet")
    assert list(enriched.columns) == REGISTER_SCHEMA.names + list(DEA_COVERAGE_COLUMNS)
    assert str(enriched["n_tenements_intersecting"].dtype) == "Int64"


def test_build_dea_coverage_refuses_tampered_source_register(tmp_path, monkeypatch):
    # Arrange as above, then append a byte to the seeded register.parquet
    # AFTER its manifest was written.
    ...
    assert result.exit_code == 1
    assert "digest" in result.output


def test_build_dea_coverage_refuses_existing_output(tmp_path, monkeypatch):
    # Run once successfully, run again with the same --date.
    ...
    assert second.exit_code == 1
    assert "refusal" in second.output
```

Fill the `...` arrange blocks by reusing `tests/test_cli.py`'s existing
seeding patterns (the build-register/build-crosswalk tests show how a
curated artefact plus manifest is produced in a tmp tree); do not invent a
parallel scaffold.

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -k "build_dea_coverage"`
Expected: FAIL — command not registered (exit code 2).

**Step 3: Write the implementation** (append to `cli.py`)

```python
@app.command("build-dea-coverage")
def build_dea_coverage(
    config: Path = ConfigOption,
    date: str = DateOption,
    catalogue_date: str = typer.Option(
        ...,
        "--catalogue-date",
        help="Dated raw/dea_stac/<date>/ snapshot to read the catalogue from.",
        callback=_validate_snapshot_date,
    ),
) -> None:
    """Enrich the latest curated register with DEA epoch coverage.

    Writes a NEW `curated/register/<date>/register.parquet` under
    `ENRICHED_REGISTER_SCHEMA`; the accepted Batch B artefact is never
    mutated. Refuses on: source register digest mismatch against its own
    manifest, catalogue snapshot verification failure, row loss/gain/
    reorder, or an existing output at `<date>`.
    """
    resolved = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    # 1. Source register: latest curated/register/<date>/, digest-verified
    #    against its own manifest's output.sha256.
    register_root = data_root / "curated" / "register"
    register_dir = _latest_curated_dated_dir(register_root, label="curated/register")
    register_path = register_dir / "register.parquet"
    register_manifest_path = Path(str(register_path) + manifests.MANIFEST_SUFFIX)
    if not register_manifest_path.exists():
        typer.echo(json.dumps({"refusal": f"no run manifest beside {register_path}"},
                              indent=2, sort_keys=True))
        raise typer.Exit(1)
    register_manifest = json.loads(register_manifest_path.read_text(encoding="utf-8"))
    actual_sha = sha256_file(register_path)
    if actual_sha != register_manifest["output"]["sha256"]:
        typer.echo(json.dumps({
            "refusal": (
                f"source register digest mismatch: {register_path} hashes "
                f"{actual_sha[:12]}..., its manifest records "
                f"{register_manifest['output']['sha256'][:12]}... -- the "
                f"artefact changed after its manifest was written"
            ),
            "stage": "source-register-digest",
        }, indent=2, sort_keys=True))
        raise typer.Exit(1)

    # 2. Catalogue snapshot: verified via SHA256SUMS.
    catalogue_dir = data_root / "raw" / "dea_stac" / catalogue_date
    _verify_snapshot_or_refuse(
        catalogue_dir, source_id="dea_stac", required_files=("catalogue_summary.json",)
    )
    catalogue_manifest_path = catalogue_dir / (
        snapshots.SHA256SUMS_FILENAME + manifests.MANIFEST_SUFFIX
    )

    # 3. Load items per source from the snapshot pages.
    items_by_source: dict[str, list] = {}
    for spec in DEA_COLLECTIONS:
        features: list = []
        for page_path in sorted((catalogue_dir / spec.collection_id).glob("items_page_*.json")):
            page = json.loads(page_path.read_text(encoding="utf-8"))
            features.extend(page.get("features") or [])
        items_by_source[spec.source_id] = features

    # 4. Coverage + enrichment.
    register_df = tables.read_table(register_path)
    item_index, duplicates_refused = dea_coverage.build_item_index(items_by_source)
    coverage_df, disclosures = dea_coverage.count_site_epochs(
        register_df, item_index, duplicates_refused=duplicates_refused
    )
    try:
        enriched = register.enrich_register_with_dea_coverage(register_df, coverage_df)
    except register.RegisterEnrichmentError as exc:
        typer.echo(json.dumps({"refusal": str(exc), "stage": "enrichment"},
                              indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # 5. Compute EVERY manifest ingredient BEFORE the artefact is written.
    #    `_write_table_or_refuse` followed by a manifest failure would strand
    #    a manifestless register.parquet that the existing-output guard then
    #    refuses to repair on the re-run -- the artefact and its provenance
    #    must fail together or land together.
    out_dir = data_root / "curated" / "register" / date
    out_path = out_dir / "register.parquet"
    _refuse_if_curated_output_already_exists(out_path, config=resolved_config, git_state=git_state)

    # `root_relative_path` takes a MAPPING (it calls `config.get`) and returns
    # `(reduced_path, root_name)`; both halves are recorded, the way
    # `fetch-maus-extract` records `source_local_path`/`_root`.
    source_register_manifest, source_register_manifest_root = manifests.root_relative_path(
        register_manifest_path, config=resolved_config
    )
    source_catalogue_manifest, source_catalogue_manifest_root = manifests.root_relative_path(
        catalogue_manifest_path, config=resolved_config
    )
    catalogue_sums_path = catalogue_dir / snapshots.SHA256SUMS_FILENAME
    catalogue_sums_sha = sha256_file(catalogue_sums_path)

    input_assets = [
        SourceAsset(
            uri=str(register_path),
            sha256=actual_sha,
            collection=None,
            snapshot_date=None,
            licence=licence.SOURCES["dmirs_001_minedex"].licence_id,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(catalogue_sums_path),
            sha256=catalogue_sums_sha,
            collection="dea_stac",
            snapshot_date=dt_date.fromisoformat(catalogue_date),
            licence="CC-BY-4.0",
            redistribute_public=True,
        ),
    ]

    # 6. Ingredients all in hand -- now write the artefact, then its manifest.
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_table_or_refuse(enriched, out_path, register.ENRICHED_REGISTER_SCHEMA)
    manifests.write_run_manifest(
        output=out_path,
        inputs=input_assets,
        config=resolved_config,
        git_state=git_state,
        resolved_args={
            "date": date,
            "catalogue_date": catalogue_date,
            "source_register_manifest": source_register_manifest,
            "source_register_manifest_root": source_register_manifest_root,
            "source_catalogue_manifest": source_catalogue_manifest,
            "source_catalogue_manifest_root": source_catalogue_manifest_root,
            "dea_coverage_disclosure": disclosures,
            "minedex_public_export_blocked": resolved.sources.minedex_public_export_blocked,
            "register_rows_before": len(register_df),
            "register_rows_after": len(enriched),
        },
    )
    typer.echo(json.dumps({
        "output": str(out_path),
        "register_rows_before": len(register_df),
        "register_rows_after": len(enriched),
        "dea_coverage_disclosure": disclosures,
        "manifest_path": str(out_path) + manifests.MANIFEST_SUFFIX,
    }, indent=2, sort_keys=True, default=str))
```

`manifests.root_relative_path(path, *, config: Mapping[str, Any]) ->
tuple[str, str]` — verified against the module: it takes the resolved-config
MAPPING (`resolved.model_dump(mode="json")`, NOT the Pydantic object; it
calls `config.get`) and returns `(reduced_path, root_name)`. `cli.py`'s
`fetch-maus-extract` is the call-site template: it unpacks both halves and
records each under its own key. The module stays authoritative over this
plan.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 12: `maus_footprints.py` — footprint SCALARS (no geometry)

**Files:**
- Create: `src/wa_mine_monitor/maus_footprints.py`
- Create: `tests/test_maus_footprints.py`

Why this task exists: D13 C5 names **Maus footprints** as an estimator
input, and the amendment above requires each site's read window to be sized
from its own footprint rather than a fixed 2,010 m constant. The repo holds
no area column anywhere, so the scalars must be derived. They are derived
ONCE into their own immutable artefact rather than recomputed inside
`derive-dea-volume`: the crosswalk's `maus_id` values come from a SPECIFIC
Maus snapshot (`sources/maus.py::_geometry_id` derives the id from clipped
geometry), so "the latest Maus snapshot" at volume time can carry different
ids and different areas than the snapshot the crosswalk was built from.
A separate digest-verifiable artefact makes that drift a refusal instead of
a silent mismatch.

Licence: the artefact is Maus-derived and stays in the CC-BY-SA-4.0 lineage
(`licence.SOURCES["maus_v2"]`), but it contains **no geometry** — only
`maus_id` and derived scalars.

**Step 1: Write the failing tests**

```python
# tests/test_maus_footprints.py
"""Tests for Maus footprint SCALARS (D13 Batch C task C5's footprint input).

Toy geopandas frames built in-test, the discipline `tests/sources/test_maus.py`
already uses; no committed geometry fixture and no geometry in any output.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from wa_mine_monitor import crosswalk
from wa_mine_monitor.maus_footprints import (
    MAUS_FOOTPRINT_STATS_SCHEMA,
    FootprintStatsError,
    derive_footprint_stats,
    join_site_footprints,
)


def _rect(x0, y0, width_m, height_m):
    return Polygon(
        [
            (x0, y0),
            (x0 + width_m, y0),
            (x0 + width_m, y0 + height_m),
            (x0, y0 + height_m),
        ]
    )


def _maus_gdf(rows, *, crs=crosswalk.TARGET_CRS):
    return gpd.GeoDataFrame(
        {"maus_id": [maus_id for maus_id, _ in rows]},
        geometry=[geometry for _, geometry in rows],
        crs=crs,
    )


def test_area_and_bounds_are_metres_in_the_equal_area_crs():
    gdf = _maus_gdf([("M1", _rect(0.0, 0.0, 900.0, 300.0))])
    stats = derive_footprint_stats(gdf)
    row = stats.iloc[0]
    assert row["maus_id"] == "M1"
    assert row["footprint_area_m2"] == pytest.approx(900.0 * 300.0)
    assert row["footprint_bbox_width_m"] == pytest.approx(900.0)
    assert row["footprint_bbox_height_m"] == pytest.approx(300.0)


def test_output_schema_carries_no_geometry_column():
    stats = derive_footprint_stats(_maus_gdf([("M1", _rect(0.0, 0.0, 100.0, 100.0))]))
    assert list(stats.columns) == MAUS_FOOTPRINT_STATS_SCHEMA.names
    assert "geometry" not in stats.columns


def test_row_order_is_deterministic_by_maus_id():
    gdf = _maus_gdf(
        [
            ("M2", _rect(0.0, 0.0, 100.0, 100.0)),
            ("M1", _rect(500.0, 0.0, 100.0, 100.0)),
        ]
    )
    assert list(derive_footprint_stats(gdf)["maus_id"]) == ["M1", "M2"]


def test_wrong_crs_is_refused_not_silently_reprojected():
    gdf = _maus_gdf([("M1", _rect(0.0, 0.0, 100.0, 100.0))], crs="EPSG:4326")
    with pytest.raises(FootprintStatsError, match="3577"):
        derive_footprint_stats(gdf)


def test_duplicate_maus_id_is_refused():
    gdf = _maus_gdf(
        [("M1", _rect(0.0, 0.0, 100.0, 100.0)), ("M1", _rect(500.0, 0.0, 100.0, 100.0))]
    )
    with pytest.raises(FootprintStatsError, match="duplicate"):
        derive_footprint_stats(gdf)


@pytest.mark.parametrize(
    "geometry", [None, Polygon(), _rect(0.0, 0.0, 0.0, 0.0)], ids=["null", "empty", "zero-area"]
)
def test_unusable_geometry_is_refused_not_dropped(geometry):
    gdf = _maus_gdf([("M1", geometry)])
    with pytest.raises(FootprintStatsError):
        derive_footprint_stats(gdf)


def _high_confidence_crosswalk(rows):
    return pd.DataFrame(rows, columns=["site_id", "maus_id", "confidence"])


def test_join_preserves_every_site_footprint_link():
    """Two sites sharing one footprint is a real shape (`shared_by_n` in
    CROSSWALK_SCHEMA exists for it): both links survive, and `maus_id` stays
    in the join output so shared and distinct footprints can be told apart."""
    stats = derive_footprint_stats(
        _maus_gdf(
            [
                ("M1", _rect(0.0, 0.0, 900.0, 300.0)),
                ("M2", _rect(5000.0, 0.0, 300.0, 300.0)),
            ]
        )
    )
    joined = join_site_footprints(
        _high_confidence_crosswalk(
            [
                {"site_id": "S1", "maus_id": "M1", "confidence": "high"},
                {"site_id": "S2", "maus_id": "M1", "confidence": "high"},
                {"site_id": "S3", "maus_id": "M2", "confidence": "high"},
            ]
        ),
        stats,
    )
    assert len(joined) == 3
    assert set(joined.columns) == {
        "site_id",
        "maus_id",
        "footprint_area_m2",
        "footprint_bbox_width_m",
        "footprint_bbox_height_m",
    }
    assert joined.loc[joined["site_id"] == "S2", "footprint_area_m2"].iloc[0] == pytest.approx(
        900.0 * 300.0
    )


def test_join_refuses_a_maus_id_absent_from_the_stats():
    stats = derive_footprint_stats(_maus_gdf([("M1", _rect(0.0, 0.0, 100.0, 100.0))]))
    with pytest.raises(FootprintStatsError, match="M9"):
        join_site_footprints(
            _high_confidence_crosswalk(
                [{"site_id": "S1", "maus_id": "M9", "confidence": "high"}]
            ),
            stats,
        )
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_maus_footprints.py -q`
Expected: `ModuleNotFoundError: No module named 'wa_mine_monitor.maus_footprints'`

**Step 3: Write the implementation**

```python
# src/wa_mine_monitor/maus_footprints.py
"""Derived footprint SCALARS from the Maus snapshot -- never geometry.

D13 C5 names Maus footprints among the volume estimator's inputs. The
estimator needs a SIZE per site, not a shape, so this module reduces each
matched polygon to three scalars (area, and bounding-box width/height in
metres) and nothing else. The CC-BY-SA geometry stays in the raw snapshot;
the scalars carry the Maus lineage forward
(`licence.SOURCES["maus_v2"]`, ShareAlike), which is why the artefact is
written under its own manifest rather than folded into an unrelated one.

Area alone cannot size a window: a long, narrow strip and a square of equal
area need very different reads. Bounding-box width and height are therefore
derived here rather than reconstructed from `sqrt(area)` downstream, where
the reconstruction would be wrong precisely for the elongated footprints
that matter most.

Every input failure REFUSES. A dropped polygon is a site that silently gets
the floor window -- an under-estimate wearing the same field names as a
measurement.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pyarrow as pa

from wa_mine_monitor import crosswalk

#: Declared output schema -- `maus_id` plus derived scalars, no geometry.
MAUS_FOOTPRINT_STATS_SCHEMA = pa.schema(
    [
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("footprint_area_m2", pa.float64(), nullable=False),
        pa.field("footprint_bbox_width_m", pa.float64(), nullable=False),
        pa.field("footprint_bbox_height_m", pa.float64(), nullable=False),
    ]
)

_JOIN_COLUMNS: tuple[str, ...] = (
    "site_id",
    "maus_id",
    "footprint_area_m2",
    "footprint_bbox_width_m",
    "footprint_bbox_height_m",
)


class FootprintStatsError(ValueError):
    """A footprint could not be reduced to trustworthy scalars -- refused."""


def derive_footprint_stats(maus_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Reduce Maus polygons to per-`maus_id` scalars, sorted by `maus_id`.

    `maus_gdf` must ALREADY be in `crosswalk.TARGET_CRS` (EPSG:3577, the
    equal-area metric CRS the crosswalk measures in) -- reprojecting here
    would silently accept a caller that never declared a CRS at all.
    """
    if maus_gdf.crs is None or str(maus_gdf.crs).upper() != crosswalk.TARGET_CRS:
        raise FootprintStatsError(
            f"maus_gdf must be projected to {crosswalk.TARGET_CRS} (equal-area, "
            f"metres) before areas are read; got {maus_gdf.crs!r}"
        )
    ids = maus_gdf["maus_id"].astype(str)
    duplicated = sorted(set(ids[ids.duplicated()]))
    if duplicated:
        raise FootprintStatsError(
            f"{len(duplicated)} duplicate maus_id(s) (first: {duplicated[0]}) -- "
            f"a duplicated footprint would double-count in the volume estimate"
        )
    rows: list[dict[str, object]] = []
    for maus_id, geometry in zip(ids, maus_gdf.geometry, strict=True):
        if geometry is None or geometry.is_empty:
            raise FootprintStatsError(
                f"maus_id {maus_id}: null or empty geometry -- refused rather "
                f"than dropped (a dropped footprint silently becomes the floor window)"
            )
        area = float(geometry.area)
        if area <= 0.0:
            raise FootprintStatsError(f"maus_id {maus_id}: non-positive area {area}")
        min_x, min_y, max_x, max_y = geometry.bounds
        rows.append(
            {
                "maus_id": maus_id,
                "footprint_area_m2": area,
                "footprint_bbox_width_m": float(max_x - min_x),
                "footprint_bbox_height_m": float(max_y - min_y),
            }
        )
    stats = pd.DataFrame(rows, columns=list(MAUS_FOOTPRINT_STATS_SCHEMA.names))
    return stats.sort_values("maus_id").reset_index(drop=True)


def join_site_footprints(
    high_confidence_crosswalk: pd.DataFrame, footprint_stats: pd.DataFrame
) -> pd.DataFrame:
    """One row per site-footprint LINK, carrying `maus_id`.

    Not reduced to `(site_id, area)`: several sites can share one footprint,
    and a site can hold more than one high-confidence link. Keeping
    `maus_id` is what lets the estimator tell a shared footprint from
    distinct ones instead of counting the same ground twice.
    """
    missing = sorted(
        set(high_confidence_crosswalk["maus_id"].dropna().astype(str))
        - set(footprint_stats["maus_id"].astype(str))
    )
    if missing:
        raise FootprintStatsError(
            f"{len(missing)} crosswalk maus_id(s) absent from the footprint "
            f"stats (first: {missing[0]}) -- the crosswalk and the footprint "
            f"artefact were built from different Maus snapshots"
        )
    joined = high_confidence_crosswalk.merge(
        footprint_stats, on="maus_id", how="left", validate="many_to_one"
    )
    return joined.loc[:, list(_JOIN_COLUMNS)].reset_index(drop=True)
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_maus_footprints.py -q`
Expected: PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 13: CLI `build-maus-footprint-areas`

**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `tests/test_cli.py`

Command shape: `build-maus-footprint-areas --config <path> --date <date>`.
Pipeline (mirroring `build-crosswalk`'s Maus handling, which is the existing
template in `cli.py`): `register.latest_snapshot(data_root, "maus_v2")` →
`_verify_snapshot_or_refuse(..., required_files=("wa_extract.gpkg",))` →
read `maus_id`/`geometry` ONLY → `.to_crs(crosswalk.TARGET_CRS)` →
`derive_footprint_stats` → compute every manifest ingredient → write
`curated/maus_footprint_areas/<date>/footprint_areas.parquet` under
`MAUS_FOOTPRINT_STATS_SCHEMA` → manifest.

Manifest requirements (D13 provenance, plus the drift guard the estimator
depends on):
- `inputs`: one `SourceAsset` for `wa_extract.gpkg` — its `sha256`, the
  snapshot date, and `licence.SOURCES["maus_v2"]`'s licence fields.
- `resolved_args`: `maus_snapshot_dir` + `maus_snapshot_dir_root` (via
  `manifests.root_relative_path(..., config=resolved_config)`, both halves),
  `maus_gpkg_sha256`, snapshot verify counts, `crs="EPSG:3577"`,
  `n_footprints`, `output_licence="CC-BY-SA-4.0"`,
  `output_share_alike=True`.

**Step 1: Write the failing tests** (append to `tests/test_cli.py`; seed the
`raw/maus_v2/<date>/` snapshot the way `tests/test_cli.py` /
`tests/sources/test_maus.py` already seed one for `build-crosswalk` — reuse
that helper, do not invent a parallel one)

```python
def test_build_maus_footprint_areas_writes_scalars_and_manifest(tmp_path, monkeypatch):
    ...  # seed a finalized raw/maus_v2/<date>/wa_extract.gpkg with 2 polygons
    result = runner.invoke(
        app,
        ["build-maus-footprint-areas", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert result.exit_code == 0, result.output
    out_path = (
        tmp_path / "data" / "curated" / "maus_footprint_areas" / "2026-08-16"
        / "footprint_areas.parquet"
    )
    stats = tables.read_table(out_path)
    assert list(stats.columns) == MAUS_FOOTPRINT_STATS_SCHEMA.names
    assert "geometry" not in stats.columns
    manifest = json.loads(
        Path(str(out_path) + ".run_manifest.json").read_text(encoding="utf-8")
    )
    args = manifest["resolved_args"]
    assert args["crs"] == "EPSG:3577"
    assert args["output_share_alike"] is True
    assert len(args["maus_gpkg_sha256"]) == 64
    assert manifest["inputs"][0]["licence"] == "CC-BY-SA-4.0"


def test_build_maus_footprint_areas_refuses_an_unverifiable_snapshot(tmp_path, monkeypatch):
    ...  # seed the snapshot, then corrupt wa_extract.gpkg after finalization
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_build_maus_footprint_areas_refuses_existing_output(tmp_path, monkeypatch):
    ...  # run twice with the same --date
    assert second.exit_code == 1
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -k "maus_footprint_areas"`
Expected (arrange filled first): FAIL with `result.exit_code == 2` and
"No such command 'build-maus-footprint-areas'".

**Step 3: Write the implementation**

Follow `build-crosswalk`'s Maus block in `cli.py` verbatim for the snapshot
resolution, verification and gpkg read (it already does exactly this,
including the `["maus_id", "geometry"]` column slice and the `to_crs`), then
`maus_footprints.derive_footprint_stats`, then the manifest-ingredients-
before-write ordering Task 11 establishes, then `_write_table_or_refuse`
under `MAUS_FOOTPRINT_STATS_SCHEMA`.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 14: `dea_volume.py` — the estimator

**Files:**
- Create: `src/wa_mine_monitor/dea_volume.py`
- Modify: `src/wa_mine_monitor/dea_coverage.py` (add `build_asset_index`)
- Create: `tests/test_dea_volume.py`
- Modify: `tests/test_dea_coverage.py`

The estimator is PURE: every input is an ordinary frame or a frozen
declaration, so no test touches geometry, the network, or the filesystem.
Amendment 1 governs this task — the fixed 2,010 m window becomes a FLOOR in
a declared `WindowPolicy`, band selection is an explicit input, asset block
metadata is read from the captured items (null when absent, never assumed
silently), and the upper bound is priced PER COLLECTION.

**Step 1a: Write the failing `build_asset_index` tests** (append to
`tests/test_dea_coverage.py`)

```python
from wa_mine_monitor.dea_coverage import (
    ASSET_INDEX_COLUMNS,
    build_asset_index,
)


def _asset_item(item_id, *, assets):
    item = _item(item_id, bbox=BBOX_A, year=1990)
    item["assets"] = assets
    return item


def test_asset_index_reads_observed_metadata_from_the_captured_item():
    items = {
        "dea_gm_ls5t": [
            _asset_item(
                "a-1990",
                assets={
                    "nbart_nir": {
                        "href": "s3://x/n.tif",
                        "file:size": 12345,
                        "proj:shape": [3200, 3200],
                        "raster:bands": [
                            {"data_type": "int16", "block_size": [512, 512]}
                        ],
                    }
                },
            )
        ]
    }
    index, disclosure = build_asset_index(items)
    assert tuple(index.columns) == ASSET_INDEX_COLUMNS
    row = index.iloc[0]
    assert row["asset_key"] == "nbart_nir"
    assert row["file_size_bytes"] == 12345
    assert row["raster_width_px"] == 3200
    assert row["block_width_px"] == 512
    assert row["data_type"] == "int16"
    assert row["bytes_per_sample"] == 2
    assert row["metadata_source"] == "stac-item-asset"
    assert disclosure["dea_gm_ls5t"]["n_assets_block_size_missing"] == 0


def test_absent_asset_metadata_stays_null_and_is_counted():
    """No implicit 512-pixel block, no implicit dtype, no implicit size --
    a missing field is null WITH a count (D13's disclosure discipline)."""
    items = {"dea_gm_ls5t": [_asset_item("a-1990", assets={"count": {"href": "s3://x/c.tif"}})]}
    index, disclosure = build_asset_index(items)
    row = index.iloc[0]
    for column in ("file_size_bytes", "block_width_px", "raster_width_px", "bytes_per_sample"):
        assert pd.isna(row[column])
    assert row["metadata_source"] == "absent"
    counts = disclosure["dea_gm_ls5t"]
    assert counts["n_assets"] == 1
    assert counts["n_assets_block_size_missing"] == 1
    assert counts["n_assets_file_size_missing"] == 1
    assert counts["n_assets_data_type_missing"] == 1


def test_an_unmapped_data_type_leaves_bytes_per_sample_null():
    items = {
        "dea_gm_ls5t": [
            _asset_item(
                "a-1990",
                assets={"count": {"raster:bands": [{"data_type": "complex128"}]}},
            )
        ]
    }
    index, _ = build_asset_index(items)
    assert pd.isna(index.iloc[0]["bytes_per_sample"])
```

**Step 1b: Write the failing estimator tests**

```python
# tests/test_dea_volume.py
"""Tests for the Tier 1 volume estimator (D13 Batch C task C5).

The estimator is pure: frames in, dict out. Every constant it rests on
arrives as a declared input (`WindowPolicy`, `CollectionSelection`,
`YearRange`) and is echoed into the output, so the estimate is recomputable
from its own record.
"""

import math

import pandas as pd
import pytest

from wa_mine_monitor.dea_volume import (
    PROVISIONAL_FIGURES,
    CollectionSelection,
    VolumePopulationError,
    WindowPolicy,
    YearRange,
    derive_volume_estimate,
)

DEFAULT_POLICY = WindowPolicy()

GM_ASSETS = ("nbart_nir", "nbart_swir_1", "nbart_swir_2", "count")
FC_ASSETS = ("bs_pc_50", "pv_pc_50", "npv_pc_50")

SELECTIONS = (
    CollectionSelection(
        source_id="dea_gm_ls5t",
        metric_ids=("nbr", "ndmi"),
        asset_keys=GM_ASSETS,
        assumed_bytes_per_pixel=2,
        assumed_tile_pixels_per_side=3200,
    ),
    CollectionSelection(
        source_id="dea_gm_ls7e",
        metric_ids=("nbr", "ndmi"),
        asset_keys=GM_ASSETS,
        assumed_bytes_per_pixel=2,
        assumed_tile_pixels_per_side=3200,
    ),
    CollectionSelection(
        source_id="dea_fc_pc",
        metric_ids=("bare_soil",),
        asset_keys=FC_ASSETS,
        assumed_bytes_per_pixel=1,
        assumed_tile_pixels_per_side=3200,
    ),
)

YEAR_RANGES = {
    "dea_gm_ls5t": YearRange(1986, 2011),
    "dea_gm_ls7e": YearRange(1999, 2021),
    "dea_fc_pc": YearRange(1987, 2025),
}


def _crosswalk(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "site_id", "maus_id", "match_method", "distance_m",
            "confidence", "ambiguity_n", "shared_by_n", "manual_review_status",
        ],
    )


def _enriched_register(rows):
    frame = pd.DataFrame(rows)
    for column in (
        "n_dea_gm_ls5t_epochs", "n_dea_gm_ls7e_epochs",
        "n_dea_gm_ls8cls9c_epochs", "n_dea_fc_pc_epochs",
    ):
        frame[column] = pd.array(frame[column], dtype="Int64")
    return frame


def _footprints(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "site_id", "maus_id", "footprint_area_m2",
            "footprint_bbox_width_m", "footprint_bbox_height_m",
        ],
    )


def _index_row(source_id, collection_id, item_id, year, *, tile="x11y22", assets="count"):
    return {
        "source_id": source_id,
        "collection_id": collection_id,
        "item_id": item_id,
        "year": year,
        "bbox_west": 116.0,
        "bbox_south": -33.0,
        "bbox_east": 117.0,
        "bbox_north": -32.0,
        "tile_id": tile,
        "product_version": "4.0.0",
        "asset_identity": assets,
    }


def _item_index():
    """TWO collections over the SAME tile-years -- so per-collection pricing
    of the upper bound is actually exercised (an LS5-only index cannot tell
    per-collection pricing apart from geomedian-pricing-for-everything)."""
    return pd.DataFrame(
        [
            _index_row("dea_gm_ls5t", "ga_ls5t_gm_cyear_3", "a-1990", 1990),
            _index_row("dea_gm_ls5t", "ga_ls5t_gm_cyear_3", "a-1991", 1991),
            _index_row("dea_fc_pc", "ga_ls_fc_pc_cyear_3", "f-1990", 1990),
            _index_row("dea_fc_pc", "ga_ls_fc_pc_cyear_3", "f-1991", 1991),
        ]
    )


def _asset_index(*, block=512, file_size=1000, dtype_bytes=2):
    rows = []
    for source_id, collection_id, item_id, keys, per_sample in (
        ("dea_gm_ls5t", "ga_ls5t_gm_cyear_3", "a-1990", GM_ASSETS, dtype_bytes),
        ("dea_fc_pc", "ga_ls_fc_pc_cyear_3", "f-1990", FC_ASSETS, 1),
    ):
        for key in keys:
            rows.append(
                {
                    "source_id": source_id,
                    "collection_id": collection_id,
                    "item_id": item_id,
                    "asset_key": key,
                    "file_size_bytes": file_size,
                    "raster_width_px": 3200,
                    "raster_height_px": 3200,
                    "block_width_px": block,
                    "block_height_px": block,
                    "data_type": "int16",
                    "bytes_per_sample": per_sample,
                    "metadata_source": "stac-item-asset",
                }
            )
    return pd.DataFrame(rows)


def _inputs():
    crosswalk = _crosswalk([
        {"site_id": "S1", "maus_id": "M1", "match_method": "contains",
         "distance_m": 0.0, "confidence": "high", "ambiguity_n": 1,
         "shared_by_n": 1, "manual_review_status": "unreviewed"},
        {"site_id": "S2", "maus_id": "M2", "match_method": "contains",
         "distance_m": 0.0, "confidence": "high", "ambiguity_n": 1,
         "shared_by_n": 1, "manual_review_status": "unreviewed"},
        {"site_id": "S3", "maus_id": None, "match_method": "none",
         "distance_m": None, "confidence": "none", "ambiguity_n": 0,
         "shared_by_n": 1, "manual_review_status": "unreviewed"},
    ])
    register = _enriched_register([
        {"site_id": "S1", "lon": 116.5, "lat": -32.5,
         "n_dea_gm_ls5t_epochs": 2, "n_dea_gm_ls7e_epochs": 0,
         "n_dea_gm_ls8cls9c_epochs": 0, "n_dea_fc_pc_epochs": 2},
        {"site_id": "S2", "lon": 116.6, "lat": -32.6,
         "n_dea_gm_ls5t_epochs": 2, "n_dea_gm_ls7e_epochs": 0,
         "n_dea_gm_ls8cls9c_epochs": 0, "n_dea_fc_pc_epochs": 2},
        {"site_id": "S3", "lon": None, "lat": None,
         "n_dea_gm_ls5t_epochs": None, "n_dea_gm_ls7e_epochs": None,
         "n_dea_gm_ls8cls9c_epochs": None, "n_dea_fc_pc_epochs": None},
    ])
    # Both footprints small enough that the floor window applies, unless a
    # test says otherwise.
    footprints = _footprints([
        {"site_id": "S1", "maus_id": "M1", "footprint_area_m2": 250_000.0,
         "footprint_bbox_width_m": 500.0, "footprint_bbox_height_m": 500.0},
        {"site_id": "S2", "maus_id": "M2", "footprint_area_m2": 250_000.0,
         "footprint_bbox_width_m": 500.0, "footprint_bbox_height_m": 500.0},
    ])
    return crosswalk, register, footprints


def _estimate(**overrides):
    crosswalk, register, footprints = _inputs()
    kwargs = {
        "crosswalk_df": crosswalk,
        "register_df": register,
        "footprints_df": footprints,
        "item_index": _item_index(),
        "asset_index": _asset_index(),
        "selections": SELECTIONS,
        "year_ranges": YEAR_RANGES,
        "window_policy": DEFAULT_POLICY,
    }
    kwargs.update(overrides)
    return derive_volume_estimate(**kwargs)


# ---------------------------------------------------------------- population


def test_population_counts_reconcile_to_high_confidence_crosswalk_rows():
    estimate = _estimate()
    assert estimate["population"]["n_sites_eligible"] == 2
    assert estimate["population"]["n_sites_unmatched"] == 1
    assert estimate["population"]["n_distinct_footprints"] == 2


def test_null_coverage_never_becomes_zero():
    crosswalk, register, footprints = _inputs()
    register.loc[0, "n_dea_gm_ls5t_epochs"] = pd.NA
    estimate = _estimate(register_df=register)
    assert estimate["population"]["n_eligible_sites_coverage_not_computed"] == 1


def test_missing_eligible_site_in_register_is_a_refusal():
    crosswalk, register, footprints = _inputs()
    with pytest.raises(VolumePopulationError, match="S2"):
        _estimate(register_df=register[register["site_id"] != "S2"])


def test_missing_footprint_for_an_eligible_site_is_a_refusal():
    """D13 C5 names Maus footprints as an input; a site with no footprint
    must not silently fall back to the floor window."""
    crosswalk, register, footprints = _inputs()
    with pytest.raises(VolumePopulationError, match="S2"):
        _estimate(footprints_df=footprints[footprints["site_id"] != "S2"])


def test_two_sites_sharing_one_footprint_count_one_distinct_footprint():
    crosswalk, register, footprints = _inputs()
    footprints.loc[1, "maus_id"] = "M1"
    estimate = _estimate(footprints_df=footprints)
    assert estimate["population"]["n_sites_eligible"] == 2
    assert estimate["population"]["n_distinct_footprints"] == 1


# -------------------------------------------------------------- window sizing


def test_a_small_footprint_gets_the_declared_floor_window():
    estimate = _estimate()
    windows = estimate["windows"]["by_site"]
    assert windows["S1"]["window_side_px"] == DEFAULT_POLICY.minimum_side_px
    assert windows["S1"]["window_side_m"] == (
        DEFAULT_POLICY.minimum_side_px * DEFAULT_POLICY.pixel_metres
    )
    assert windows["S1"]["window_sizing"] == "floor"


def test_a_large_footprint_grows_the_window_beyond_the_floor():
    crosswalk, register, footprints = _inputs()
    footprints.loc[0, "footprint_bbox_width_m"] = 9_000.0
    footprints.loc[0, "footprint_bbox_height_m"] = 1_000.0
    footprints.loc[0, "footprint_area_m2"] = 9_000.0 * 1_000.0
    estimate = _estimate(footprints_df=footprints)
    policy = DEFAULT_POLICY
    expected_px = (
        math.ceil((9_000.0 + 2 * policy.reference_buffer_metres) / policy.pixel_metres)
        + policy.alignment_pad_px
    )
    assert estimate["windows"]["by_site"]["S1"]["window_side_px"] == expected_px
    assert estimate["windows"]["by_site"]["S1"]["window_sizing"] == "footprint"
    # S2 is untouched: sizing is PER SITE, not one window for the population.
    assert estimate["windows"]["by_site"]["S2"]["window_side_px"] == policy.minimum_side_px


def test_an_elongated_footprint_is_sized_by_its_long_span_not_sqrt_area():
    """A 9,000 x 1,000 m strip and a 3,000 x 3,000 m square have the same
    area; only the span rule covers the strip."""
    crosswalk, register, footprints = _inputs()
    footprints.loc[0, "footprint_bbox_width_m"] = 9_000.0
    footprints.loc[0, "footprint_bbox_height_m"] = 1_000.0
    footprints.loc[0, "footprint_area_m2"] = 9_000_000.0
    strip = _estimate(footprints_df=footprints)["windows"]["by_site"]["S1"]
    equivalent_square_side_m = math.sqrt(9_000_000.0)
    assert strip["window_side_m"] > equivalent_square_side_m


def test_the_window_covers_the_footprint_plus_two_buffers():
    crosswalk, register, footprints = _inputs()
    footprints.loc[0, "footprint_bbox_width_m"] = 4_000.0
    footprints.loc[0, "footprint_bbox_height_m"] = 4_000.0
    footprints.loc[0, "footprint_area_m2"] = 16_000_000.0
    window = _estimate(footprints_df=footprints)["windows"]["by_site"]["S1"]
    assert window["window_side_m"] >= 4_000.0 + 2 * DEFAULT_POLICY.reference_buffer_metres


# ------------------------------------------------------------------- selection


def test_band_selection_is_an_input_not_a_hard_coded_count():
    fewer = tuple(
        selection
        if selection.source_id != "dea_gm_ls5t"
        else CollectionSelection(
            source_id="dea_gm_ls5t",
            metric_ids=("nbr",),
            asset_keys=("nbart_nir", "nbart_swir_2"),
            assumed_bytes_per_pixel=2,
            assumed_tile_pixels_per_side=3200,
        )
        for selection in SELECTIONS
    )
    baseline = _estimate()
    reduced = _estimate(selections=fewer)
    assert reduced["selections"]["dea_gm_ls5t"]["n_assets_selected"] == 2
    assert baseline["selections"]["dea_gm_ls5t"]["n_assets_selected"] == len(GM_ASSETS)
    assert reduced["bytes"]["windowed_read_bytes_estimate"] < (
        baseline["bytes"]["windowed_read_bytes_estimate"]
    )


def test_an_asset_outside_the_pinned_spec_is_refused():
    bad = (
        CollectionSelection(
            source_id="dea_gm_ls5t",
            metric_ids=("nbr",),
            asset_keys=("not_an_asset",),
            assumed_bytes_per_pixel=2,
            assumed_tile_pixels_per_side=3200,
        ),
    )
    with pytest.raises(ValueError, match="not_an_asset"):
        _estimate(selections=bad)


def test_a_duplicated_selected_asset_is_refused():
    bad = (
        CollectionSelection(
            source_id="dea_gm_ls5t",
            metric_ids=("nbr",),
            asset_keys=("nbart_nir", "nbart_nir"),
            assumed_bytes_per_pixel=2,
            assumed_tile_pixels_per_side=3200,
        ),
    )
    with pytest.raises(ValueError, match="duplicate"):
        _estimate(selections=bad)


# ----------------------------------------------------------------- arithmetic


def test_windowed_byte_arithmetic_is_per_collection_and_reproducible():
    estimate = _estimate()
    window_px = DEFAULT_POLICY.minimum_side_px**2
    # Two eligible sites, 2 LS5 epochs each, 4 assets, 2 B/px;
    # 2 FC epochs each, 3 assets, 1 B/px. LS7 has 0 epochs.
    gm_raw = 2 * 2 * len(GM_ASSETS) * window_px * 2
    fc_raw = 2 * 2 * len(FC_ASSETS) * window_px * 1
    expected = int((gm_raw + fc_raw) * estimate["assumptions"]["compression_ratio"])
    assert estimate["bytes"]["windowed_read_bytes_estimate"] == expected
    by_collection = estimate["bytes"]["windowed_read_bytes_by_collection"]
    assert by_collection["dea_gm_ls7e"] == 0


def test_shared_tiles_are_not_counted_as_repeated_full_downloads():
    estimate = _estimate()
    assert estimate["tiles"]["n_distinct_tiles"] == 1
    assert estimate["tiles"]["n_distinct_tile_years_by_collection"] == {
        "dea_gm_ls5t": 2,
        "dea_gm_ls7e": 0,
        "dea_fc_pc": 2,
    }


def test_the_upper_bound_is_priced_per_collection_never_geomedian_for_all():
    """FC is uint8 with 3 selected assets; geomedian is int16 with 4. Pricing
    every tile-year at geomedian rates would silently overstate FC and, in
    the reverse case, drop collections from the bound entirely."""
    estimate = _estimate()
    tile_px = 3200**2
    expected_gm = 2 * tile_px * len(GM_ASSETS) * 2
    expected_fc = 2 * tile_px * len(FC_ASSETS) * 1
    by_collection = estimate["bytes"]["upper_bound_bytes_by_collection"]
    assert by_collection["dea_gm_ls5t"] == expected_gm
    assert by_collection["dea_fc_pc"] == expected_fc
    assert estimate["bytes"]["upper_bound_bytes"] == expected_gm + expected_fc
    assert estimate["bytes"]["upper_bound_bytes"] > (
        estimate["bytes"]["windowed_read_bytes_estimate"]
    )


def test_sensor_overlap_years_are_counted_per_collection_never_merged():
    crosswalk, register, footprints = _inputs()
    register.loc[0, "n_dea_gm_ls7e_epochs"] = 2
    register.loc[1, "n_dea_gm_ls7e_epochs"] = 2
    per_collection = _estimate(register_df=register)["site_year_windows"]["per_collection"]
    assert per_collection["dea_gm_ls5t"] == 4
    assert per_collection["dea_gm_ls7e"] == 4


def test_years_outside_a_declared_range_are_excluded_with_a_count():
    estimate = _estimate(
        year_ranges={**YEAR_RANGES, "dea_gm_ls5t": YearRange(1991, 2011)}
    )
    assert estimate["tiles"]["n_distinct_tile_years_by_collection"]["dea_gm_ls5t"] == 1
    assert estimate["year_range_disclosure"]["dea_gm_ls5t"]["n_item_years_outside_range"] == 1


# ------------------------------------------------------- asset-metadata nulls


def test_range_requests_come_from_observed_block_metadata():
    estimate = _estimate()
    # 67-px window against 512-px blocks: 1 x 1 block per band.
    per_window_band = 1
    total_bands = 2 * 2 * len(GM_ASSETS) + 2 * 2 * len(FC_ASSETS)
    assert estimate["expected_range_requests"] == total_bands * per_window_band
    assert estimate["asset_metadata_disclosure"]["n_assets_block_size_missing"] == 0


def test_missing_block_metadata_yields_null_range_requests_with_a_count():
    """No implicit 4-requests-per-window-band: the number is null and the
    absence is counted."""
    asset_index = _asset_index()
    asset_index["block_width_px"] = pd.NA
    asset_index["block_height_px"] = pd.NA
    estimate = _estimate(asset_index=asset_index)
    assert estimate["expected_range_requests"] is None
    assert estimate["asset_metadata_disclosure"]["n_assets_block_size_missing"] > 0


def test_observed_bytes_per_sample_overrides_the_declared_assumption_and_says_so():
    asset_index = _asset_index(dtype_bytes=4)
    estimate = _estimate(asset_index=asset_index)
    gm = estimate["selections"]["dea_gm_ls5t"]
    assert gm["bytes_per_pixel"] == 4
    assert gm["bytes_per_pixel_source"] == "observed"
    assert estimate["selections"]["dea_gm_ls7e"]["bytes_per_pixel_source"] == "assumed"


def test_a_collection_with_no_asset_metadata_falls_back_to_the_declared_assumption():
    estimate = _estimate(asset_index=_asset_index().iloc[0:0])
    for source_id in ("dea_gm_ls5t", "dea_fc_pc"):
        assert estimate["selections"][source_id]["bytes_per_pixel_source"] == "assumed"
    assert estimate["expected_range_requests"] is None


# --------------------------------------------------------------- the record


def test_provisional_figures_are_comparison_fields_only():
    estimate = _estimate()
    comparison = estimate["provisional_figures_comparison_only"]
    assert comparison == PROVISIONAL_FIGURES
    assert comparison == {
        "provisional_n_tiles": 367,
        "provisional_bytes_estimate": 350 * 10**9,
        "provisional_bytes_upper_bound": int(2.3 * 10**12),
    }
    assert estimate["bytes"]["upper_bound_bytes"] != comparison["provisional_bytes_upper_bound"]


def test_every_declared_input_is_echoed_into_the_output():
    estimate = _estimate()
    assert estimate["window_policy"] == {
        "pixel_metres": DEFAULT_POLICY.pixel_metres,
        "minimum_side_px": DEFAULT_POLICY.minimum_side_px,
        "reference_buffer_metres": DEFAULT_POLICY.reference_buffer_metres,
        "alignment_pad_px": DEFAULT_POLICY.alignment_pad_px,
    }
    assert estimate["year_ranges"]["dea_gm_ls5t"] == [1986, 2011]
    assert estimate["selections"]["dea_fc_pc"]["metric_ids"] == ["bare_soil"]
    assert "windowed_read_bytes_estimate" in estimate["formulas"]
    assert "upper_bound_bytes" in estimate["formulas"]
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dea_coverage.py -q -k "asset_index or asset_metadata"`
Expected: `ImportError: cannot import name 'build_asset_index'`

Run: `uv run pytest tests/test_dea_volume.py -q`
Expected: `ModuleNotFoundError: No module named 'wa_mine_monitor.dea_volume'`

**Step 3a: `build_asset_index`** (append to `dea_coverage.py`)

```python
#: Column order of the asset index. Every metadata field is NULLABLE: DEA
#: STAC items do not uniformly carry `file:size`, `proj:shape` or
#: `raster:bands`, and a field this module invented would be indistinguishable
#: from one the source published.
ASSET_INDEX_COLUMNS: tuple[str, ...] = (
    "source_id",
    "collection_id",
    "item_id",
    "asset_key",
    "file_size_bytes",
    "raster_width_px",
    "raster_height_px",
    "block_width_px",
    "block_height_px",
    "data_type",
    "bytes_per_sample",
    "metadata_source",
)

#: Fixed keys of the per-collection asset-metadata disclosure.
ASSET_METADATA_DISCLOSURE_KEYS: tuple[str, ...] = (
    "n_assets",
    "n_assets_file_size_missing",
    "n_assets_block_size_missing",
    "n_assets_raster_shape_missing",
    "n_assets_data_type_missing",
)

#: DECLARED dtype widths. A dtype outside this table leaves
#: `bytes_per_sample` NULL rather than guessing a width.
_BYTES_PER_SAMPLE: dict[str, int] = {
    "uint8": 1, "int8": 1,
    "uint16": 2, "int16": 2,
    "uint32": 4, "int32": 4, "float32": 4,
    "uint64": 8, "int64": 8, "float64": 8,
}


def build_asset_index(
    items_by_source: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """Normalise captured item ASSET metadata into one nullable-typed frame.

    Returns ``(asset_index, disclosures)``. Nothing is defaulted: a missing
    block size stays null and is counted, so a downstream range-request
    figure is either derived from published metadata or absent -- never a
    plausible-looking constant (the amendment's 1c requirement).
    """
    rows: list[dict[str, Any]] = []
    disclosures: dict[str, dict[str, int]] = {}
    for source_id, items in items_by_source.items():
        collection_id = spec_for_source(source_id).collection_id
        counts = dict.fromkeys(ASSET_METADATA_DISCLOSURE_KEYS, 0)
        for item in items:
            item_id = str(item.get("id"))
            for asset_key, asset in (item.get("assets") or {}).items():
                asset = asset or {}
                file_size = asset.get("file:size")
                shape = asset.get("proj:shape") or [None, None]
                bands = asset.get("raster:bands") or [{}]
                band = bands[0] if bands else {}
                block = band.get("block_size") or [None, None]
                data_type = band.get("data_type")
                counts["n_assets"] += 1
                counts["n_assets_file_size_missing"] += int(file_size is None)
                counts["n_assets_block_size_missing"] += int(block[0] is None)
                counts["n_assets_raster_shape_missing"] += int(shape[0] is None)
                counts["n_assets_data_type_missing"] += int(data_type is None)
                observed = any(
                    value is not None for value in (file_size, shape[0], block[0], data_type)
                )
                rows.append(
                    {
                        "source_id": source_id,
                        "collection_id": collection_id,
                        "item_id": item_id,
                        "asset_key": str(asset_key),
                        "file_size_bytes": file_size,
                        # `proj:shape` is [height, width].
                        "raster_height_px": shape[0],
                        "raster_width_px": shape[1] if len(shape) > 1 else None,
                        "block_height_px": block[0],
                        "block_width_px": block[1] if len(block) > 1 else None,
                        "data_type": data_type,
                        "bytes_per_sample": _BYTES_PER_SAMPLE.get(str(data_type)) ,
                        "metadata_source": "stac-item-asset" if observed else "absent",
                    }
                )
        disclosures[source_id] = counts
    index = pd.DataFrame(rows, columns=list(ASSET_INDEX_COLUMNS))
    for column in (
        "file_size_bytes", "raster_width_px", "raster_height_px",
        "block_width_px", "block_height_px", "bytes_per_sample",
    ):
        index[column] = pd.array(index[column], dtype="Int64")
    return index, disclosures
```

**Step 3b: Write the estimator**

```python
# src/wa_mine_monitor/dea_volume.py
"""Tier 1 data-volume estimate from real populations (D13 Batch C task C5).

Replaces the provisional full-WA figures (367 tiles / 350 GB / 2.3 TB) with
an estimate derived from the ACTUAL high-confidence crosswalk population,
per-site Maus footprint SCALARS, the enriched register's per-collection
epoch counts, the captured STAC item index and the captured asset metadata.
The provisional figures are carried in the output as COMPARISON FIELDS ONLY
-- never computational constants (a figure inherited from an earlier
document and never re-derived is not established by being repeated).

Three disciplines shape the interface:

1. **Every constant is a declared INPUT.** `WindowPolicy`,
   `CollectionSelection` and `YearRange` arrive from the caller and are
   echoed verbatim into the output, so the estimate is recomputable from its
   own record. The old fixed 2,010 m window survives only as
   `WindowPolicy.minimum_side_px` -- a FLOOR, not the answer.
2. **Sizing comes from the footprint, not the point.** A site's window is
   sized from its Maus bounding-box span; `sqrt(area)` is not used, because
   it under-covers exactly the long, narrow footprints that matter most.
   The MINEDEX point remains a Tier 0 diagnostic only (D13 C3 acceptance).
3. **Nothing is silently assumed.** Bytes-per-pixel prefers OBSERVED
   `bytes_per_sample` from the asset index and falls back to the caller's
   declared assumption WITH a `bytes_per_pixel_source` label. Range requests
   are computed only from observed block sizes; where those are absent the
   figure is `None` and the absence is counted.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from wa_mine_monitor.source_catalogue import spec_for_source

#: The Tier 1 population rule lives in `crosswalk.tier1_population`
#: (`confidence == "high"`, design doc D1); it is imported rather than
#: restated so the two can never drift.
from wa_mine_monitor.crosswalk import tier1_population


@dataclass(frozen=True)
class WindowPolicy:
    """How a footprint becomes a read window.

    ``minimum_side_px`` (67 px = 2,010 m at 30 m) is the FLOOR the old
    fixed-window constant became: below it, reads are dominated by
    per-request overhead rather than pixels. ``reference_buffer_metres``
    is added on EACH side so the window carries surrounding reference
    ground, not just the disturbance. ``alignment_pad_px`` covers arbitrary
    raster-grid alignment -- a window that fits exactly still straddles one
    extra pixel when it lands off-grid.
    """

    pixel_metres: int = 30
    minimum_side_px: int = 67
    reference_buffer_metres: int = 300
    alignment_pad_px: int = 1


@dataclass(frozen=True)
class CollectionSelection:
    """What this estimate intends to READ from one collection.

    ``asset_keys`` must be a non-empty, duplicate-free subset of the pinned
    `SourceSpec.asset_roles`: the spec says what every item must CARRY, this
    says what the run intends to FETCH, and conflating the two is how a band
    count becomes a hard-coded 10.
    """

    source_id: str
    metric_ids: tuple[str, ...]
    asset_keys: tuple[str, ...]
    assumed_bytes_per_pixel: int
    assumed_tile_pixels_per_side: int


@dataclass(frozen=True)
class YearRange:
    first_year: int
    last_year: int


#: Carried for comparison ONLY -- see the module docstring.
PROVISIONAL_FIGURES: dict[str, int] = {
    "provisional_n_tiles": 367,
    "provisional_bytes_estimate": 350 * 10**9,
    "provisional_bytes_upper_bound": int(2.3 * 10**12),
}

#: The one remaining declared scalar the inputs cannot supply.
VOLUME_ASSUMPTIONS: dict[str, Any] = {
    # Deflate on real-valued COGs. Declared, not measured -- and applied to
    # the windowed estimate only, never to the upper bound (whose whole
    # point is to be conservative).
    "compression_ratio": 0.6,
}

_FORMULAS: dict[str, str] = {
    "window_side_px": (
        "max(minimum_side_px, ceil((max(bbox_width_m, bbox_height_m) + "
        "2 * reference_buffer_metres) / pixel_metres) + alignment_pad_px)"
    ),
    "windowed_read_bytes_estimate": (
        "sum over collections of: sum over eligible sites of "
        "(window_side_px^2 * site_epochs * n_assets_selected * "
        "bytes_per_pixel), times compression_ratio"
    ),
    "upper_bound_bytes": (
        "sum over collections of: n_distinct_tile_years(collection) * "
        "tile_pixels_per_side^2 * n_assets_selected(collection) * "
        "bytes_per_pixel(collection) -- every distinct tile-year fetched "
        "WHOLE and uncompressed, priced at ITS OWN collection's rates; "
        "shared tiles counted once"
    ),
    "expected_range_requests": (
        "sum over collections of: sum over eligible sites of "
        "(ceil(window_side_px / block_width_px) * "
        "ceil(window_side_px / block_height_px) * site_epochs * "
        "n_assets_selected) -- null when block metadata is absent"
    ),
    "scratch_space_bytes": "upper_bound_bytes (worst case: whole tiles staged)",
}


class VolumePopulationError(ValueError):
    """The eligible population failed reconciliation against its inputs."""


def _epoch_column(source_id: str) -> str:
    from wa_mine_monitor.dea_coverage import DEA_EPOCH_COLUMN_BY_SOURCE

    return DEA_EPOCH_COLUMN_BY_SOURCE[source_id]


def _validate_selections(selections: Sequence[CollectionSelection]) -> None:
    for selection in selections:
        spec = spec_for_source(selection.source_id)
        if not selection.asset_keys:
            raise ValueError(f"{selection.source_id}: no assets selected")
        if len(set(selection.asset_keys)) != len(selection.asset_keys):
            raise ValueError(f"{selection.source_id}: duplicate selected asset key(s)")
        unknown = [key for key in selection.asset_keys if key not in spec.asset_roles]
        if unknown:
            raise ValueError(
                f"{selection.source_id}: selected asset(s) {unknown} are not in the "
                f"pinned spec's asset_roles -- the selection names bands the "
                f"catalogue never promised"
            )


def _window_for(span_metres: float, policy: WindowPolicy) -> tuple[int, str]:
    footprint_px = (
        math.ceil((span_metres + 2 * policy.reference_buffer_metres) / policy.pixel_metres)
        + policy.alignment_pad_px
    )
    if footprint_px <= policy.minimum_side_px:
        return policy.minimum_side_px, "floor"
    return footprint_px, "footprint"


def derive_volume_estimate(
    *,
    crosswalk_df: pd.DataFrame,
    register_df: pd.DataFrame,
    footprints_df: pd.DataFrame,
    item_index: pd.DataFrame,
    asset_index: pd.DataFrame,
    selections: Sequence[CollectionSelection],
    year_ranges: Mapping[str, YearRange],
    window_policy: WindowPolicy,
) -> dict[str, Any]:
    """Derive the Tier 1 volume estimate; every count reconciles or refuses.

    Eligible population: `crosswalk.tier1_population`'s distinct site_ids.
    Every eligible site must exist in the enriched register AND carry at
    least one footprint (refusal otherwise). Eligible sites whose coverage is
    NULL are excluded WITH A COUNT -- null never becomes zero.
    """
    _validate_selections(selections)

    high = tier1_population(crosswalk_df)
    eligible_sites = sorted(set(high["site_id"]))
    all_sites = set(register_df["site_id"])
    missing = [site for site in eligible_sites if site not in all_sites]
    if missing:
        raise VolumePopulationError(
            f"{len(missing)} eligible site(s) absent from the enriched register "
            f"(first: {missing[0]}) -- population fails reconciliation"
        )
    without_footprint = [
        site for site in eligible_sites if site not in set(footprints_df["site_id"])
    ]
    if without_footprint:
        raise VolumePopulationError(
            f"{len(without_footprint)} eligible site(s) carry no Maus footprint "
            f"(first: {without_footprint[0]}) -- refusing rather than sizing them "
            f"at the floor window, which would understate the read silently"
        )

    unmatched = sorted(all_sites - set(eligible_sites))
    eligible = register_df[register_df["site_id"].isin(eligible_sites)]
    epoch_columns = [_epoch_column(s.source_id) for s in selections]
    coverage_null = eligible[epoch_columns].isna().any(axis=1)
    computable = eligible[~coverage_null]

    # -- window per site: the LARGEST span across that site's footprints,
    #    so a site linked to two footprints is sized to cover both.
    windows: dict[str, dict[str, Any]] = {}
    for site_id in sorted(set(computable["site_id"])):
        links = footprints_df[footprints_df["site_id"] == site_id]
        span = float(
            links[["footprint_bbox_width_m", "footprint_bbox_height_m"]].to_numpy().max()
        )
        side_px, sizing = _window_for(span, window_policy)
        windows[site_id] = {
            "window_side_px": side_px,
            "window_side_m": side_px * window_policy.pixel_metres,
            "window_sizing": sizing,
            "footprint_span_m": span,
        }

    # -- bytes-per-pixel and block sizes: OBSERVED where the captured items
    #    published them, the caller's declared assumption otherwise, always
    #    labelled.
    selection_records: dict[str, dict[str, Any]] = {}
    for selection in selections:
        observed = asset_index[
            (asset_index["source_id"] == selection.source_id)
            & (asset_index["asset_key"].isin(selection.asset_keys))
        ]
        observed_bpp = observed["bytes_per_sample"].dropna()
        observed_block_w = observed["block_width_px"].dropna()
        observed_block_h = observed["block_height_px"].dropna()
        observed_shape = observed["raster_width_px"].dropna()
        selection_records[selection.source_id] = {
            "collection_id": spec_for_source(selection.source_id).collection_id,
            "metric_ids": list(selection.metric_ids),
            "asset_keys": list(selection.asset_keys),
            "n_assets_selected": len(selection.asset_keys),
            "bytes_per_pixel": int(observed_bpp.max())
            if len(observed_bpp)
            else selection.assumed_bytes_per_pixel,
            "bytes_per_pixel_source": "observed" if len(observed_bpp) else "assumed",
            "tile_pixels_per_side": int(observed_shape.max())
            if len(observed_shape)
            else selection.assumed_tile_pixels_per_side,
            "tile_pixels_per_side_source": "observed" if len(observed_shape) else "assumed",
            "block_width_px": int(observed_block_w.max()) if len(observed_block_w) else None,
            "block_height_px": int(observed_block_h.max()) if len(observed_block_h) else None,
        }

    # -- windowed read bytes, per collection.
    per_collection_epochs: dict[str, int] = {}
    windowed_by_collection: dict[str, int] = {}
    range_requests: int | None = 0
    for selection in selections:
        record = selection_records[selection.source_id]
        column = _epoch_column(selection.source_id)
        raw = 0
        epochs_total = 0
        requests = 0
        for site_id, window in windows.items():
            site_epochs = int(computable.loc[computable["site_id"] == site_id, column].iloc[0])
            epochs_total += site_epochs
            pixels = window["window_side_px"] ** 2
            raw += pixels * site_epochs * record["n_assets_selected"] * record["bytes_per_pixel"]
            if record["block_width_px"] and record["block_height_px"]:
                requests += (
                    math.ceil(window["window_side_px"] / record["block_width_px"])
                    * math.ceil(window["window_side_px"] / record["block_height_px"])
                    * site_epochs
                    * record["n_assets_selected"]
                )
        per_collection_epochs[selection.source_id] = epochs_total
        windowed_by_collection[selection.source_id] = int(
            raw * VOLUME_ASSUMPTIONS["compression_ratio"]
        )
        if record["block_width_px"] is None or record["block_height_px"] is None:
            # Block metadata absent for this collection -- the TOTAL becomes
            # null rather than a partial sum wearing a complete label.
            range_requests = None
        elif range_requests is not None:
            range_requests += requests

    # -- distinct tiles / tile-years intersecting eligible sites, within the
    #    declared year ranges, PER COLLECTION.
    located = computable[computable["lon"].notna() & computable["lat"].notna()]
    tiles: set[str] = set()
    tile_years_by_collection: dict[str, set[tuple[str, int]]] = {
        selection.source_id: set() for selection in selections
    }
    year_range_disclosure: dict[str, dict[str, int]] = {
        selection.source_id: {"n_item_years_outside_range": 0} for selection in selections
    }
    for _, item in item_index.iterrows():
        source_id = str(item["source_id"])
        if source_id not in tile_years_by_collection:
            continue
        hits = (
            (located["lon"] >= item["bbox_west"])
            & (located["lon"] <= item["bbox_east"])
            & (located["lat"] >= item["bbox_south"])
            & (located["lat"] <= item["bbox_north"])
        )
        if not bool(hits.any()):
            continue
        year = int(item["year"])
        window = year_ranges.get(source_id)
        if window is not None and not (window.first_year <= year <= window.last_year):
            year_range_disclosure[source_id]["n_item_years_outside_range"] += 1
            continue
        tiles.add(str(item["tile_id"]))
        tile_years_by_collection[source_id].add((str(item["tile_id"]), year))

    upper_by_collection: dict[str, int] = {}
    for selection in selections:
        record = selection_records[selection.source_id]
        upper_by_collection[selection.source_id] = int(
            len(tile_years_by_collection[selection.source_id])
            * record["tile_pixels_per_side"] ** 2
            * record["n_assets_selected"]
            * record["bytes_per_pixel"]
        )

    asset_disclosure = {
        "n_assets": int(len(asset_index)),
        "n_assets_block_size_missing": int(asset_index["block_width_px"].isna().sum()),
        "n_assets_file_size_missing": int(asset_index["file_size_bytes"].isna().sum()),
        "n_assets_data_type_missing": int(asset_index["bytes_per_sample"].isna().sum()),
    }
    upper_bound = sum(upper_by_collection.values())

    return {
        "population": {
            "n_sites_eligible": len(eligible_sites),
            "n_sites_unmatched": len(unmatched),
            "n_eligible_sites_coverage_not_computed": int(coverage_null.sum()),
            "n_distinct_footprints": int(
                footprints_df[footprints_df["site_id"].isin(eligible_sites)]["maus_id"].nunique()
            ),
        },
        "windows": {
            "by_site": windows,
            "n_sites_at_floor": sum(
                1 for w in windows.values() if w["window_sizing"] == "floor"
            ),
        },
        "tiles": {
            "n_distinct_tiles": len(tiles),
            "n_distinct_tile_years_by_collection": {
                source_id: len(pairs) for source_id, pairs in tile_years_by_collection.items()
            },
        },
        "site_year_windows": {"per_collection": per_collection_epochs},
        "selections": selection_records,
        "year_ranges": {
            source_id: [window.first_year, window.last_year]
            for source_id, window in year_ranges.items()
        },
        "year_range_disclosure": year_range_disclosure,
        "window_policy": {
            "pixel_metres": window_policy.pixel_metres,
            "minimum_side_px": window_policy.minimum_side_px,
            "reference_buffer_metres": window_policy.reference_buffer_metres,
            "alignment_pad_px": window_policy.alignment_pad_px,
        },
        "bytes": {
            "windowed_read_bytes_estimate": sum(windowed_by_collection.values()),
            "windowed_read_bytes_by_collection": windowed_by_collection,
            "upper_bound_bytes": upper_bound,
            "upper_bound_bytes_by_collection": upper_by_collection,
            "scratch_space_bytes": upper_bound,
        },
        "expected_range_requests": range_requests,
        "asset_metadata_disclosure": asset_disclosure,
        "assumptions": dict(VOLUME_ASSUMPTIONS),
        "formulas": dict(_FORMULAS),
        "provisional_figures_comparison_only": dict(PROVISIONAL_FIGURES),
    }
```

Implementer's note: the per-site loops above are written for clarity over a
few-thousand-row register; if `pytest` wall-clock suffers, vectorise them —
the tests pin the ARITHMETIC, not the loop shape.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dea_volume.py -q`
Expected: PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 15: CLI `derive-dea-volume`

**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `tests/test_cli.py`

Command shape: `derive-dea-volume --config <path> --date <output date>`.
Pipeline: locate the latest `curated/crosswalk/<date>/`, the latest
`curated/maus_footprint_areas/<date>/`, and the latest ENRICHED
`curated/register/<date>/` (the latest register dir whose parquet carries
the four coverage columns — refuse with a message naming `build-dea-coverage`
if the latest register is unenriched) → digest-verify all three against their
manifests (same check as Task 11) → **refuse unless the crosswalk manifest
and the footprint-area manifest record the SAME Maus GeoPackage sha256** →
rebuild the item index and the asset index from the catalogue snapshot named
in the enriched register's manifest (`resolved_args.catalogue_date`) →
`derive_volume_estimate` → write
`<data_root>/reports/dea-volume/<date>/estimate.json` (+ run manifest with
`output=estimate.json`, inputs = register/crosswalk/footprints/catalogue
assets, `resolved_args` carrying the four source-manifest digests). No public
export: everything stays under `data_root`.

The Maus-digest equality check is the whole reason the footprint scalars are
their own artefact: `maus_id` is derived from clipped geometry
(`sources/maus.py::_geometry_id`), so a crosswalk built from one Maus
snapshot and footprints derived from another can join cleanly on ids that
no longer mean the same polygon. Equal digests are the only cheap proof
they came from the same snapshot.

**Step 1: Write the failing test** (append to `tests/test_cli.py` — one
end-to-end test; arrange by chaining the already-tested seams: seed register
→ fetch catalogue (fake client) → `build-dea-coverage` →
`build-maus-footprint-areas` → seed a crosswalk parquet + manifest the way
the existing build-crosswalk tests do)

```python
def test_derive_dea_volume_writes_estimate_with_manifest_digests(tmp_path, monkeypatch):
    ...  # arrange chain as above
    result = runner.invoke(
        app, ["derive-dea-volume", "--config", str(cfg_file), "--date", "2026-08-18"]
    )
    assert result.exit_code == 0, result.output
    estimate_path = (
        tmp_path / "data" / "reports" / "dea-volume" / "2026-08-18" / "estimate.json"
    )
    estimate = json.loads(estimate_path.read_text(encoding="utf-8"))
    for key in (
        "population", "windows", "tiles", "site_year_windows", "selections",
        "window_policy", "year_ranges", "bytes", "expected_range_requests",
        "asset_metadata_disclosure", "assumptions", "formulas",
        "provisional_figures_comparison_only", "source_manifest_digests",
    ):
        assert key in estimate
    assert set(estimate["source_manifest_digests"]) == {
        "register", "crosswalk", "footprints", "catalogue",
    }
    assert (Path(str(estimate_path) + ".run_manifest.json")).exists()


def test_derive_dea_volume_refuses_an_unenriched_register(tmp_path, monkeypatch):
    ...  # arrange with ONLY the Batch B register (no build-dea-coverage run)
    result = runner.invoke(
        app, ["derive-dea-volume", "--config", str(cfg_file), "--date", "2026-08-18"]
    )
    assert result.exit_code == 1
    assert "build-dea-coverage" in result.output


def test_derive_dea_volume_refuses_mismatched_maus_digests(tmp_path, monkeypatch):
    """Crosswalk and footprints built from DIFFERENT Maus snapshots: the ids
    can still join, so only the digests catch it."""
    ...  # arrange the full chain, then rewrite the footprint manifest's
    ...  # recorded maus_gpkg_sha256 to a different digest
    assert result.exit_code == 1
    assert "maus" in result.output.lower()
```

**Step 2: Run the tests to verify they fail**

The `...` arrange blocks above are placeholders, not runnable test code:
fill them from the Task 7/11/13 helpers FIRST, then run the red step and
check the failure message says what it should. Until they are filled the
tests fail on the missing arrangement, which proves nothing about the
command.

Run: `uv run pytest tests/test_cli.py -q -k "derive_dea_volume"`
Expected (arrange filled): FAIL with `result.exit_code == 2` and "No such
command 'derive-dea-volume'" in `result.output` — the arrange chain
(`fetch-dea-catalogue`, `build-dea-coverage`, `build-maus-footprint-areas`)
already exists by this task, so the only missing piece is the command
itself. Verify the message names `derive-dea-volume`; any other failure
means the arrange is wrong, not the implementation.

**Step 3: Write the implementation** (append to `cli.py`; reuse the Task 11
digest-verification block — extract it into a helper
`_digest_verified_manifest(artefact_path) -> dict` used by every command
that reads a curated artefact, returning the parsed manifest or raising the
structured refusal). The command body:

1. `_load_config_or_exit`, git state.
2. Latest crosswalk dir, footprint-areas dir and register dir;
   `_digest_verified_manifest` on all three parquets.
3. Read the enriched register; if any of
   `register.DEA_COVERAGE_COLUMNS` is absent, structured refusal:
   `"latest curated register is not DEA-enriched -- run build-dea-coverage first"`.
4. Compare `crosswalk_manifest["resolved_args"]["maus_gpkg_sha256"]` (the
   `build-crosswalk` manifest already records the Maus gpkg among its
   `inputs`; read whichever field that command actually writes — the module
   is authoritative) against the footprint manifest's own
   `maus_gpkg_sha256`. Unequal → structured refusal naming both digests
   (truncated) and both artefacts.
5. `catalogue_date = register_manifest["resolved_args"]["catalogue_date"]`;
   `_verify_snapshot_or_refuse` the snapshot; load item pages exactly as in
   Task 11 (extract that loop into a shared helper
   `_load_dea_items(catalogue_dir) -> dict[str, list]`), then
   `dea_coverage.build_item_index` and `dea_coverage.build_asset_index`.
6. Build the declared inputs: `selections` (one `CollectionSelection` per
   pinned collection, asset keys a subset of each `SourceSpec.asset_roles`),
   `year_ranges` from each captured `collection.json`'s temporal extent
   (recorded in `catalogue_summary.json` — NOT invented here), and
   `WindowPolicy()`. Echo all three into `resolved_args`.
7. `footprints = maus_footprints.join_site_footprints(
   crosswalk.tier1_population(crosswalk_df), footprint_stats)`.
8. `estimate = dea_volume.derive_volume_estimate(...)`; catch
   `VolumePopulationError` and `ValueError` from selection validation →
   structured refusals.
9. `estimate["source_manifest_digests"] = {"register": ..., "crosswalk": ...,
   "footprints": ..., "catalogue": ...}` (each `sha256_file` of the
   respective manifest).
10. Compute every manifest ingredient, THEN write `estimate.json` (refuse if
    it exists — `_refuse_if_curated_output_already_exists` works on any
    path), then `write_run_manifest(output=estimate_path, inputs=[register,
    crosswalk, footprints, catalogue assets], resolved_args={...})` — the
    Task 11 ordering rule applies here too.
11. Echo the JSON summary (population, bytes, tiles, output path,
    manifest path).

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS.

**Step 5: Run the quality battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean.

---

### Task 16: Batch C acceptance — fixture end-to-end, export block, checkpoint

**Files:**
- Create: `tests/test_batch_c_acceptance.py`
- Create: `docs/checkpoints/batch-c-result.md`

**Step 1: Write the failing acceptance tests**

```python
# tests/test_batch_c_acceptance.py
"""Batch C acceptance (D13 task C6): fixture-driven end-to-end chain.

Chains fetch-dea-catalogue -> build-dea-coverage -> build-maus-footprint-
areas -> derive-dea-volume over committed synthetic fixtures in one tmp
tree, then asserts the manifest chain, the disclosure reconciliation, and
the D7 export block. Reuses the arrange helpers test_cli.py built for Tasks
7/11/13/15 -- import them, do not duplicate them.
"""

import json

import pytest

from wa_mine_monitor import export_gate, tables


def test_end_to_end_chain_manifests_and_reconciliation(tmp_path, monkeypatch):
    # Arrange + run all four commands via the Task 7/11/13/15 helpers.
    ...
    # 1. Every manifest in the chain digest-verifies: catalogue SHA256SUMS
    #    manifest, footprint-areas manifest, enriched register manifest,
    #    estimate manifest -- re-hash each output, compare output.sha256.
    # 2. The enriched register's manifest names the catalogue manifest;
    #    the estimate names all FOUR source digests.
    # 3. Coverage disclosures reconcile: computed + not_computed == rows,
    #    for all four collections.
    # 4. The estimate's windowed-read and upper-bound bytes differ from the
    #    provisional constants, and every window is sized from a footprint
    #    or the declared floor -- never from the MINEDEX point.
    ...


def test_enriched_register_public_export_is_blocked(tmp_path, monkeypatch):
    """D7: MINEDEX-derived rows carry redistribute_public=False semantics,
    so export_public refuses the enriched register whole."""
    ...  # arrange through build-dea-coverage, read the enriched register
    enriched = tables.read_table(out_path)
    frame = enriched.assign(redistribute_public=False)
    with pytest.raises(PermissionError, match="licence gate"):
        export_gate.export_public(frame)
```

Note: if the enriched register does not already carry a
`redistribute_public` column, the acceptance test documents the actual
export seam — read how Batch B's checkpoint/`export_gate` tests gate the
Batch B register and mirror that exactly; the assertion that must hold is
that NO code path publishes the enriched register while
`minedex_public_export_blocked` is true.

**Step 2: Run the tests to verify they fail, then implement the arrange
blocks until they pass**

Run: `uv run pytest tests/test_batch_c_acceptance.py -q`
Expected: FAIL first (missing arrange), then PASS once wired.

**Step 3: Write the checkpoint skeleton** (`docs/checkpoints/batch-c-result.md`)

```markdown
# Batch C result — DEA catalogue, epoch coverage, volume re-derivation

Status: PENDING LIVE RUN. The fixture acceptance suite passes
(`tests/test_batch_c_acceptance.py`); the figures below are filled by the
live run and are empty until it happens.

## Live run record

- Fetch date (`--date` of `fetch-dea-catalogue`): _pending_
- Collection extent dates (temporal extent read from each captured
  `collection.json` — NOT the fetch date): _pending_
- Product version (`odc:dataset_version` from captured items): _pending_
- Per-collection live item counts (must all be non-zero): _pending_
- Snapshot verify counts (ok/bad/missing): _pending_
- Coverage disclosures (all four collections; computed + zero +
  not_computed reconciled against register rows): _pending_
- Footprint scalars: n footprints, min/median/max area, how many sites size
  at the declared floor window vs. their own footprint: _pending_
- Volume estimate: eligible sites, distinct footprints, distinct tiles and
  per-collection tile-years, windowed-read bytes (per collection),
  upper-bound bytes (per collection), scratch space, expected range requests
  (or null with its disclosure counts): _pending_
- Asset-metadata completeness: observed vs. missing `file:size`, block size
  and dtype per collection — and, for every figure that fell back to a
  declared assumption, which assumption and why: _pending_
- Provisional figures replaced (comparison): 367 tiles / 350 GB / 2.3 TB
  vs. measured: _pending_

## Gates

- The enriched register remains INTERNAL (D7 closed; manifest records
  `minedex_public_export_blocked: true`).
- The volume report selects the execution host from measured scratch-space
  need, not a fixed machine assumption — decision recorded here after the
  live run.
```

**Step 4: Run the full battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean, full suite green.

**Step 5: Live acceptance run (after review, on the real data root)**

This is the one step that touches the network, and it is a CLI run, not a
test. In order, with the project config:

```
uv run wa-mine-monitor fetch-dea-catalogue --config config/base.yaml --date <today>
uv run wa-mine-monitor build-dea-coverage --config config/base.yaml --date <today> --catalogue-date <today>
uv run wa-mine-monitor build-maus-footprint-areas --config config/base.yaml --date <today>
uv run wa-mine-monitor derive-dea-volume --config config/base.yaml --date <today>
```

Then fill every `_pending_` field in `docs/checkpoints/batch-c-result.md`
from the commands' JSON output and the captured snapshot — the checkpoint
distinguishes fetch date, collection extent date, and product version (three
different dates; conflating them is the "verbatim match is not a current
figure" failure). Before writing any count into the checkpoint, reconcile it
against its own totals (computed + not_computed = rows; the four collections'
item counts against `catalogue_summary.json`).

---

## Execution order and review notes

- Tasks 1–3 (http), 4 (catalogue), 5–6 (dea source) have no repo-state
  dependencies and can build in that order immediately; 7 depends on 4–6;
  8–9 depend on 4 (`spec_for_source`) and pandas; 10 depends on 8–9; 11
  depends on 7 and 10; 12 depends on nothing but geopandas and `crosswalk`;
  13 depends on 12; 14 depends on 4, 8–9 and 12's output SHAPE only (its
  tests use synthetic frames); 15 depends on 11, 13 and 14; 16 depends on
  everything.
- Tasks 12–13 (the Maus footprint scalars) were added when the pre-build
  amendment's Finding 1a was applied: D13 C5 names Maus footprints as an
  estimator input, the repo held no area column anywhere, and deriving the
  scalars inside `derive-dea-volume` would have read "the latest Maus
  snapshot" — which can differ from the one the crosswalk's `maus_id`
  values came from. A separate digest-verifiable artefact turns that drift
  into a refusal (Task 15, step 4).
- Where this plan quotes an existing helper's signature (`root_relative_path`,
  `_latest_curated_dated_dir`, `_verify_snapshot_or_refuse`,
  `DateOption`/`_validate_snapshot_date`), the MODULE is authoritative, not
  the plan — read it before calling.
- Nothing in this batch commits, publishes, or exports; the enriched
  register and the volume report stay under `data_root`, internal, per D7.
