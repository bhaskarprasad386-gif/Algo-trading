"""Free/public macro intelligence sourced from official RBI endpoints.

This module deliberately avoids paid feeds and never fabricates a value.  RBI's
public mobile homepage is used for current policy rates; the RBI DBIE public
API is used for corroborating macro context when available.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/intelligence", tags=["External Intelligence"])

_RBI_HOME = "https://m.rbi.org.in/home.aspx"
_DBIE_HOME = "https://dbie.rbihub.in/"
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL = 300.0


def _cached(key: str) -> dict[str, Any] | None:
    item = _CACHE.get(key)
    if item and time.time() - item[0] < _TTL:
        return item[1]
    return None


def _put(key: str, value: dict[str, Any]) -> dict[str, Any]:
    _CACHE[key] = (time.time(), value)
    return value


def _rbi_snapshot() -> dict[str, Any]:
    cached = _cached("rbi")
    if cached:
        return cached
    now = datetime.now(timezone.utc).isoformat()
    try:
        r = requests.get(
            _RBI_HOME,
            timeout=8,
            headers={"User-Agent": "AlgoTrading-External-Intelligence/1.0"},
        )
        r.raise_for_status()
        text = re.sub(r"\s+", " ", r.text)
        def rate(label: str) -> float | None:
            m = re.search(re.escape(label) + r"\s*[:|]\s*([0-9]+(?:\.[0-9]+)?)\s*%", text, re.I)
            return float(m.group(1)) if m else None
        current = rate("Policy Repo Rate")
        sdf = rate("Standing Deposit Facility Rate")
        msf = rate("Marginal Standing Facility Rate")
        bank = rate("Bank Rate")
        reverse = rate("Fixed Reverse Repo Rate")
        as_at = None
        m = re.search(r"As at\s+([^<]{4,80}?)(?:\s*\(Source\s*:\s*FBIL\)|$)", text, re.I)
        if m:
            as_at = m.group(1).strip(" .")
        value = {
            "status": "LIVE" if current is not None else "SOURCE_PENDING",
            "source": "RBI official public rates page",
            "url": _RBI_HOME,
            "checked_at": now,
            "as_at": as_at,
            "policy_repo_rate": current,
            "sdf_rate": sdf,
            "msf_rate": msf,
            "bank_rate": bank,
            "fixed_reverse_repo_rate": reverse,
            "statement": "Current policy-rate values are read from RBI's public Current Rates section. The policy-resolution archive is linked separately; no statement text is inferred.",
            "policy_statement_url": "https://www.rbi.org.in/Scripts/Annualpolicy.aspx",
        }
        return _put("rbi", value)
    except Exception as exc:
        return _put("rbi", {
            "status": "ERROR",
            "source": "RBI official public rates page",
            "url": _RBI_HOME,
            "checked_at": now,
            "error": str(exc),
            "policy_repo_rate": None,
            "statement": "RBI source could not be reached; no value was fabricated.",
            "policy_statement_url": "https://www.rbi.org.in/Scripts/Annualpolicy.aspx",
        })


def _dbie_snapshot() -> dict[str, Any]:
    cached = _cached("dbie")
    if cached:
        return cached
    now = datetime.now(timezone.utc).isoformat()
    try:
        r = requests.get(
            _DBIE_HOME,
            timeout=8,
            headers={"User-Agent": "AlgoTrading-External-Intelligence/1.0"},
        )
        r.raise_for_status()
        text = re.sub(r"\s+", " ", r.text)
        def indicator(label: str) -> str | None:
            m = re.search(re.escape(label) + r".{0,180}?([0-9]+(?:\.[0-9]+)?)\s*%", text, re.I)
            return m.group(1) if m else None
        value = {
            "status": "LIVE",
            "source": "RBI DBIE public database",
            "url": _DBIE_HOME,
            "checked_at": now,
            "policy_repo_rate": indicator("Policy repo rate"),
            "cpi_inflation": indicator("CPI inflation"),
            "real_gdp_growth": indicator("Real GDP growth"),
            "gsec_10y": indicator("10-year G-sec yield"),
        }
        return _put("dbie", value)
    except Exception as exc:
        return _put("dbie", {
            "status": "ERROR",
            "source": "RBI DBIE public database",
            "url": _DBIE_HOME,
            "checked_at": now,
            "error": str(exc),
        })


@router.get("/macro")
def macro_intelligence() -> dict[str, Any]:
    rbi = _rbi_snapshot()
    dbie = _dbie_snapshot()
    current = rbi.get("policy_repo_rate")
    corroborated = dbie.get("policy_repo_rate")
    previous = None
    if isinstance(current, (int, float)) and isinstance(corroborated, (int, float)) and current != corroborated:
        previous = corroborated
    change = (float(current) - float(previous)) if previous is not None else None
    return {
        "status": "success",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "repo_rate": {
            "current": current,
            "previous": previous,
            "change": change,
            "status": rbi.get("status", "SOURCE_PENDING"),
        },
        "policy": {
            "statement": rbi.get("statement"),
            "source": rbi.get("source"),
            "url": rbi.get("policy_statement_url"),
            "as_at": rbi.get("as_at"),
        },
        "indicators": {
            "cpi_inflation": dbie.get("cpi_inflation"),
            "real_gdp_growth": dbie.get("real_gdp_growth"),
            "gsec_10y": dbie.get("gsec_10y"),
        },
        "sources": [rbi, dbie],
        "live_orders": "OFF",
    }
