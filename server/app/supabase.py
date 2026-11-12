"""Thin helpers over the Supabase PostgREST API, shared by the ingest scripts.

Every request goes through `_send`, which retries transient network failures.
Ingest runs last the better part of an hour, so a single dropped connection
must not take the whole run down.
"""

import os
import time
from typing import Any

import httpx2 as httpx
from dotenv import load_dotenv

MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 2.0

# Transient by nature: retrying is safe because upserts and the progress patch
# are both idempotent.
TRANSIENT_STATUS = {502, 503, 504, 408, 429}

# Postgres's "canceling statement due to statement timeout", which PostgREST
# returns as a plain 500. Any other 500 is a real error (a bad statement, a
# constraint) and is surfaced at once, but this one is the database host
# stalling: the same refresh call that times out at 8s runs in a fraction of a
# second a minute later. Retrying is safe - every refresh function is one
# transaction, rolled back whole when it is cancelled.
STATEMENT_TIMEOUT = "57014"


def _is_transient(response: httpx.Response) -> bool:
    if response.status_code in TRANSIENT_STATUS:
        return True
    return response.status_code == 500 and STATEMENT_TIMEOUT in response.text


def _send(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Issue a request, retrying timeouts, connection drops, gateway 5xx and
    statement timeouts."""
    last_error: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(BACKOFF_SECONDS * (2 ** (attempt - 1)))
        try:
            response = client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            continue

        if _is_transient(response) and attempt < MAX_ATTEMPTS - 1:
            last_error = RuntimeError(f"HTTP {response.status_code}")
            continue
        return response

    raise RuntimeError(f"{method} {url} failed after {MAX_ATTEMPTS} attempts: {last_error}")


def credentials() -> tuple[str, str]:
    """Read Supabase credentials from .env locally or the environment in CI.

    Raises RuntimeError with an actionable message when either is missing.
    """
    load_dotenv()
    missing = [
        name
        for name in ("SUPABASE_URL", "SUPABASE_SECRET_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            f"missing {', '.join(missing)}. Set them in .env locally, or as "
            "repository secrets for the scheduled workflow."
        )
    return os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_SECRET_KEY"]


def headers(secret_key: str) -> dict[str, str]:
    return {
        "apikey": secret_key,
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }


def count_rows(
    client: httpx.Client,
    base: str,
    hdrs: dict[str, str],
    table: str,
    params: dict[str, str] | None = None,
) -> int:
    """Exact row count, read off the Content-Range header."""
    response = _send(
        client,
        "GET",
        f"{base}/rest/v1/{table}",
        params={"select": "*", "limit": "1", **(params or {})},
        headers={**hdrs, "Prefer": "count=exact"},
    )
    response.raise_for_status()
    return int(response.headers.get("content-range", "*/0").split("/")[-1])


PAGE_SIZE = 1000


def select(
    client: httpx.Client,
    base: str,
    hdrs: dict[str, str],
    table: str,
    params: dict[str, str],
) -> list[dict[str, Any]]:
    """Every row a filtered read matches, paged past PostgREST's 1000-row cap.

    `params` must carry an `order`, or the pages are not guaranteed to line up.
    """
    rows: list[dict[str, Any]] = []
    while True:
        response = _send(
            client,
            "GET",
            f"{base}/rest/v1/{table}",
            params={**params, "offset": str(len(rows)), "limit": str(PAGE_SIZE)},
            headers=hdrs,
        )
        response.raise_for_status()
        page = response.json()
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows


def upsert(
    client: httpx.Client,
    base: str,
    hdrs: dict[str, str],
    table: str,
    rows: list[dict[str, Any]],
    on_conflict: str = "id",
) -> None:
    response = _send(
        client,
        "POST",
        f"{base}/rest/v1/{table}",
        params={"on_conflict": on_conflict},
        headers={**hdrs, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=rows,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"upsert into {table} failed ({response.status_code}): {response.text}")


def rpc(
    client: httpx.Client,
    base: str,
    hdrs: dict[str, str],
    function: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    """Call a Postgres function through PostgREST and return its result.

    A single-value function comes back as a bare JSON scalar, a set-returning
    one as a list of objects.
    """
    response = _send(
        client,
        "POST",
        f"{base}/rest/v1/rpc/{function}",
        headers=hdrs,
        json=payload or {},
    )
    if response.status_code >= 400:
        raise RuntimeError(f"rpc {function} failed ({response.status_code}): {response.text}")
    return response.json()


def patch(
    client: httpx.Client,
    base: str,
    hdrs: dict[str, str],
    table: str,
    match: dict[str, str],
    values: dict[str, Any],
) -> None:
    response = _send(
        client,
        "PATCH",
        f"{base}/rest/v1/{table}",
        params=match,
        headers={**hdrs, "Prefer": "return=minimal"},
        json=values,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"patch on {table} failed ({response.status_code}): {response.text}")
