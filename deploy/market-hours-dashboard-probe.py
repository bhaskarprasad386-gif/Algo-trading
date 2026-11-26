#!/usr/bin/env python3
"""Low-concurrency market-hours dashboard probe (stdlib only).

Run from the VPS or a trusted workstation:
  python3 deploy/market-hours-dashboard-probe.py --base-url https://YOUR_API_HOST

Optional authentication is read from ALGO_PROBE_BEARER_TOKEN. The token is
never printed or written to the report. Defaults are intentionally gentle:
2 concurrent requests, 5-second sampling interval, 60-second duration.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ENDPOINTS = (
    "/health/live",
    "/health",
    "/api/v1/market-data/live-health",
    "/api/v1/market-data/runtime/health",
    "/api/v1/market-data/common-feed-health",
    "/api/v1/market-data/overview",
    "/api/v1/market-data/shared-index-runner/health-lite",
)


def percentile(values: list[float], percent: float) -> float | None:
    """Return a linearly interpolated percentile without third-party packages."""
    if not values:
        return None
    if not 0 <= percent <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _safe_health_fields(path: str, payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    result: dict = {}
    pool = payload.get("database_pool")
    if isinstance(pool, dict):
        result["database_pool"] = {
            key: pool.get(key)
            for key in (
                "checkedout", "capacity", "long_checkout_count",
                "oldest_checkout_seconds", "checkout_wait_count",
                "average_checkout_wait_seconds", "max_checkout_wait_seconds",
                "pool_timeout_count", "active_checkouts",
                "checkout_tracking_status",
            )
            if key in pool
        }
    event_loop = payload.get("event_loop")
    if isinstance(event_loop, dict):
        result["event_loop"] = {
            key: event_loop.get(key)
            for key in (
                "sample_interval_seconds", "sample_count", "last_lag_seconds",
                "average_lag_seconds", "max_lag_seconds", "p95_lag_bucket",
            )
            if key in event_loop
        }
    if path.endswith("/live-health"):
        runtime = payload.get("runtime_feed")
        if isinstance(runtime, dict):
            result["feed_freshness"] = {
                "status": runtime.get("status"),
                "age_seconds": runtime.get("age_seconds"),
                "exchange_freshness": runtime.get("exchange_freshness"),
            }
    if path.endswith("/overview"):
        rows = payload.get("indices")
        if isinstance(rows, list):
            result["overview_quotes"] = [
                {
                    "symbol": row.get("symbol"),
                    "status": row.get("status"),
                    "quote_cache_age_seconds": row.get("quote_cache_age_seconds"),
                }
                for row in rows
                if isinstance(row, dict)
            ]
    return result


def probe_one(base_url: str, path: str, timeout: float, token: str | None) -> dict:
    url = base_url.rstrip("/") + path
    headers = {"Accept": "application/json", "User-Agent": "algo-market-hours-probe/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(url, headers=headers, method="GET")
    started = time.perf_counter()
    status = None
    error = None
    payload: object = None
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read(2_000_001)
            if len(raw) <= 2_000_000:
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = None
            else:
                error = "response_too_large"
    except HTTPError as exc:
        status = int(exc.code)
        error = "http_error"
    except (URLError, TimeoutError, OSError) as exc:
        error = type(exc).__name__
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "endpoint": path,
        "latency_ms": round(elapsed_ms, 3),
        "status_code": status,
        "error": error,
        "health": _safe_health_fields(path, payload),
    }


def run_probe(
    base_url: str,
    *,
    duration_seconds: float = 60.0,
    interval_seconds: float = 5.0,
    timeout_seconds: float = 4.5,
    concurrency: int = 2,
    token: str | None = None,
) -> dict:
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url must start with http:// or https://")
    if duration_seconds <= 0 or interval_seconds < 2 or timeout_seconds <= 0:
        raise ValueError("duration/timeout must be positive and interval must be at least 2 seconds")
    if not 1 <= concurrency <= 4:
        raise ValueError("concurrency must be between 1 and 4")

    started_wall = datetime.now(timezone.utc)
    started = time.monotonic()
    deadline = started + duration_seconds
    samples: list[dict] = []
    batches = 0

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="dashboard-probe") as pool:
        while time.monotonic() < deadline:
            batch_started = time.monotonic()
            futures = [
                pool.submit(probe_one, base_url, path, timeout_seconds, token)
                for path in ENDPOINTS
            ]
            for future in as_completed(futures):
                try:
                    samples.append(future.result())
                except Exception as exc:
                    samples.append({
                        "endpoint": "probe_internal",
                        "latency_ms": 0.0,
                        "status_code": None,
                        "error": type(exc).__name__,
                        "health": {},
                    })
            batches += 1
            remaining = interval_seconds - (time.monotonic() - batch_started)
            if remaining > 0 and time.monotonic() < deadline:
                time.sleep(min(remaining, max(0.0, deadline - time.monotonic())))

    by_endpoint = {}
    for path in ENDPOINTS:
        rows = [row for row in samples if row["endpoint"] == path]
        latencies = [float(row["latency_ms"]) for row in rows]
        status_counts: dict[str, int] = {}
        error_counts: dict[str, int] = {}
        for row in rows:
            status_key = str(row["status_code"]) if row["status_code"] is not None else "NO_HTTP_RESPONSE"
            status_counts[status_key] = status_counts.get(status_key, 0) + 1
            if row["error"]:
                error_counts[row["error"]] = error_counts.get(row["error"], 0) + 1
        by_endpoint[path] = {
            "requests": len(rows),
            "p50_ms": round(percentile(latencies, 50), 3) if latencies else None,
            "p95_ms": round(percentile(latencies, 95), 3) if latencies else None,
            "p99_ms": round(percentile(latencies, 99), 3) if latencies else None,
            "max_ms": round(max(latencies), 3) if latencies else None,
            "status_counts": status_counts,
            "error_counts": error_counts,
            "last_health_sample": next(
                (row["health"] for row in reversed(rows) if row["health"]), {}
            ),
        }

    ended_wall = datetime.now(timezone.utc)
    return {
        "probe": "market-hours-dashboard",
        "started_at": started_wall.isoformat(),
        "ended_at": ended_wall.isoformat(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "interval_seconds": interval_seconds,
        "timeout_seconds": timeout_seconds,
        "max_concurrency": concurrency,
        "batches": batches,
        "total_requests": len(samples),
        "total_errors": sum(1 for row in samples if row["error"]),
        "endpoints": by_endpoint,
        "note": (
            "This probe measures HTTP latency and exposed health fields only. "
            "It does not measure VPS CPU/RSS; collect those separately with pidstat/free."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="API origin, e.g. https://api.example.com")
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=4.5)
    parser.add_argument("--concurrency", type=int, default=2, help="bounded to 1..4")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    parser.add_argument(
        "--token-env", default="ALGO_PROBE_BEARER_TOKEN",
        help="environment variable containing an optional bearer token",
    )
    args = parser.parse_args()
    token = os.environ.get(args.token_env) or None
    report = run_probe(
        args.base_url,
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        timeout_seconds=args.timeout_seconds,
        concurrency=args.concurrency,
        token=token,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
