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
can carry credentials (an API key travels as a query param on some sources),
and an exception message ends up in logs and structured refusals. SILO is
fetched anonymously from its open-data bucket in this project, so no
credential of its own is at risk here, but the redaction is unconditional
because the client is shared across sources that do carry one.
"""

from __future__ import annotations

import email.utils
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
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
                raise HttpRequestRefused(f"HTTP {status} for {redacted_url(url)} -- not retryable")
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

    def get_json(self, url: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return self.get(url, params=params).json()

    def get_text(self, url: str, *, params: Mapping[str, Any] | None = None) -> str:
        return self.get(url, params=params).text

    def get_bytes(self, url: str, *, params: Mapping[str, Any] | None = None) -> bytes:
        return self.get(url, params=params).content

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
            except Exception as exc:  # noqa: BLE001 -- captured to pick the lowest-index failure, not swallowed
                if tolerate_errors:
                    slots[index] = None
                elif first_error is None or index < first_error[0]:
                    first_error = (index, exc)
    if first_error is not None:
        raise first_error[1]
    return slots
