"""Weekly three-swing scanner; real daily bars are aggregated to weekly bars."""
from datetime import date, datetime, timedelta, time as clock_time
import asyncio
from pathlib import Path
import json
from zoneinfo import ZoneInfo
from statistics import mean, pstdev
from math import isfinite
import threading
import time
from fastapi import APIRouter, HTTPException, Query

from app.market_data.nifty200_universe import NIFTY200_SYMBOLS

router = APIRouter(prefix="/api/v1/scanner/weekly-swing", tags=["Weekly Swing Scanner"])

# Static universe avoids a hard dependency on the NiftyIndices CSV being reachable
# from the VPS. Review this list after future NIFTY 200 rebalances.
SCAN_BATCH_SIZE = 50
SCAN_BATCH_INTERVAL_SECONDS = 300
HISTORICAL_REQUEST_PAUSE_SECONDS = 0.4
NSE_CAPITAL_MARKET_HOLIDAYS = {
    date(2026, 1, 15), date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 26),
    date(2026, 3, 31), date(2026, 4, 3), date(2026, 4, 14),
    date(2026, 5, 1), date(2026, 5, 28), date(2026, 6, 26),
    date(2026, 9, 14), date(2026, 10, 2), date(2026, 10, 20),
    date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 25),
}
CACHE_SCHEMA_VERSION = 1
_SCAN_LOCK = threading.Lock()
_SCAN_STATE = {
    "status": "idle", "universe": "NIFTY 200", "total": 200, "scanned": 0,
    "failed": 0, "matches": [], "failures": [], "current_symbol": None, "started_at": None, "finished_at": None,
}
_SCAN_THREAD = None


def _nifty200_symbols():
    """Return the static NIFTY 200 snapshot without any network dependency."""
    return list(NIFTY200_SYMBOLS)


def _cache_root():
    """Store downloaded candles beside the configured durable market-data DB."""
    from app.core.config import settings
    configured_db = Path(settings.BACKTEST_DATA_DB)
    base = configured_db if configured_db.is_absolute() else (Path.cwd() / configured_db)
    return base.with_name(base.name + ".weekly_swing_cache")


def _cache_path(ticker):
    return _cache_root() / (ticker.replace("/", "_") + ".json")


def _load_cached_rows(ticker):
    path = _cache_path(ticker)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != CACHE_SCHEMA_VERSION or payload.get("symbol") != ticker:
            return [], None
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return [], None
        by_day = {}
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            try:
                day = date.fromisoformat(str(row[0])[:10])
                values = [float(value) for value in row[1:6]]
                if not all(isfinite(value) for value in values):
                    continue
                if min(values[:4]) <= 0 or values[4] < 0:
                    continue
            except (TypeError, ValueError, IndexError):
                continue
            by_day[day.isoformat()] = row
        valid = [by_day[key] for key in sorted(by_day)]
        last_day = date.fromisoformat(sorted(by_day)[-1]) if by_day else None
        return valid, last_day
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return [], None


def _save_cached_rows(ticker, rows):
    path = _cache_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    payload = {"schema_version": CACHE_SCHEMA_VERSION, "symbol": ticker,
               "saved_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(), "rows": rows}
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _nse_today():
    """Return the exchange-local date, independent of the server's timezone."""
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _is_nse_trading_day(day):
    """Return whether a date is a known NSE cash-market trading day."""
    if day.weekday() >= 5:
        return False
    if day.year == 2026:
        return day not in NSE_CAPITAL_MARKET_HOLIDAYS
    return False


def _nse_market_is_open_now():
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday() >= 5:
        return False
    if now.date().year == 2026 and now.date() in NSE_CAPITAL_MARKET_HOLIDAYS:
        return False
    return clock_time(9, 15) <= now.time() < clock_time(15, 30)


def _fetch_daily_range(token, historical, start, end, on_chunk=None):
    """Fetch history in bounded chunks; persist each successful chunk immediately."""
    rows = []
    cursor = start
    while cursor <= end:
        if _nse_market_is_open_now():
            raise HTTPException(
                status_code=409,
                detail="Historical scanner downloads paused because NSE market hours have started; resume after 15:30 IST.",
            )
        chunk_end = min(cursor + timedelta(days=300), end)
        response = historical.get_candles(
            exchange="NSE", symboltoken=str(token), interval="ONE_DAY",
            from_date=f"{cursor} 09:15", to_date=f"{chunk_end} 15:30",
        )
        time.sleep(HISTORICAL_REQUEST_PAUSE_SECONDS)
        if not isinstance(response, dict) or not response.get("status") or not isinstance(response.get("data"), list):
            raise ValueError("Historical candle response invalid")
        chunk_rows = response["data"]
        rows.extend(chunk_rows)
        if on_chunk is not None:
            on_chunk(chunk_rows)
        cursor = chunk_end + timedelta(days=1)
    return rows


def _history_fetch_ranges(cached_rows, end):
    """Return the initial backfill, detectable interior gaps, and the uncached tail."""
    start_date = end - timedelta(days=365 * 5)
    dates = sorted({
        date.fromisoformat(str(row[0])[:10])
        for row in cached_rows
        if isinstance(row, list) and row and date.fromisoformat(str(row[0])[:10]) <= end
    })
    if not dates:
        return [(start_date, end)]
    ranges = []
    for previous, following in zip(dates, dates[1:]):
        gap_start, gap_end = previous + timedelta(days=1), following - timedelta(days=1)
        if (gap_end - gap_start).days >= 4:
            ranges.append((gap_start, gap_end))
    if dates[-1] < end:
        ranges.append((dates[-1] + timedelta(days=1), end))
    return [(start, finish) for start, finish in ranges if start <= finish]


def _scan_one_symbol(ticker, master, historical, end):
    instrument = master.get_instrument(ticker, "NSE")
    if not instrument and not ticker.endswith("-EQ"):
        instrument = master.get_instrument(f"{ticker}-EQ", "NSE")
    if not instrument or not instrument.get("token"):
        raise ValueError("Instrument/token not found in Angel One master")

    cached_rows, last_day = _load_cached_rows(ticker)
    cached_rows = [row for row in cached_rows if date.fromisoformat(str(row[0])[:10]) <= end]
    last_day = date.fromisoformat(str(cached_rows[-1][0])[:10]) if cached_rows else None
    indexed = {str(row[0])[:10]: row for row in cached_rows if isinstance(row, list) and row}
    fetch_ranges = _history_fetch_ranges(cached_rows, end)
    if fetch_ranges:
        def persist_chunk(chunk_rows):
            indexed.update({str(row[0])[:10]: row for row in chunk_rows if isinstance(row, list) and row})
            _save_cached_rows(ticker, [indexed[k] for k in sorted(indexed)])

        for start, finish in fetch_ranges:
            _fetch_daily_range(instrument["token"], historical, start, finish, on_chunk=persist_chunk)
        cached_rows = [indexed[k] for k in sorted(indexed)]

    result = analyze(ticker, weekly_bars(cached_rows))
    if result.get("status") == "insufficient_data":
        raise ValueError("Insufficient weekly history")
    result["history_cache"] = "updated" if fetch_ranges else "reused"
    result["history_last_day"] = str(cached_rows[-1][0])[:10] if cached_rows else None
    return result


def _run_nifty200_scan():
    global _SCAN_STATE
    from app.market_data.client import MarketDataClient
    from app.market_data.historical import HistoricalDataClient
    from app.market_data.instruments import InstrumentMaster

    with _SCAN_LOCK:
        _SCAN_STATE = {
            "status": "running", "universe": "NIFTY 200", "total": 0, "scanned": 0,
            "failed": 0, "matches": [], "failures": [], "current_symbol": None,
            "started_at": time.time(), "finished_at": None, "batch_size": SCAN_BATCH_SIZE,
            "cache_mode": "persistent_daily_incremental",
        }
    try:
        symbols = _nifty200_symbols()
        master = InstrumentMaster()
        historical = HistoricalDataClient(MarketDataClient())
        end = _nse_today()
        with _SCAN_LOCK:
            _SCAN_STATE["total"] = len(symbols)
            _SCAN_STATE["batches_total"] = (len(symbols) + SCAN_BATCH_SIZE - 1) // SCAN_BATCH_SIZE

        for batch_start in range(0, len(symbols), SCAN_BATCH_SIZE):
            if _nse_market_is_open_now():
                raise RuntimeError("NSE market hours: start the historical scan after 15:30 IST")
            batch = symbols[batch_start:batch_start + SCAN_BATCH_SIZE]
            batch_started = time.monotonic()
            with _SCAN_LOCK:
                _SCAN_STATE["batch_number"] = batch_start // SCAN_BATCH_SIZE + 1
            for ticker in batch:
                with _SCAN_LOCK:
                    _SCAN_STATE["current_symbol"] = ticker
                try:
                    result = _scan_one_symbol(ticker, master, historical, end)
                    if result.get("matches"):
                        with _SCAN_LOCK:
                            _SCAN_STATE["matches"].extend(result["matches"])
                except HTTPException as exc:
                    if exc.status_code == 409:
                        raise
                    with _SCAN_LOCK:
                        _SCAN_STATE["failed"] += 1
                        _SCAN_STATE["failures"].append({"symbol": ticker, "reason": str(exc.detail)[:180]})
                except Exception as exc:
                    with _SCAN_LOCK:
                        _SCAN_STATE["failed"] += 1
                        _SCAN_STATE["failures"].append({"symbol": ticker, "reason": str(exc)[:180]})
                finally:
                    with _SCAN_LOCK:
                        _SCAN_STATE["scanned"] += 1
            if batch_start + SCAN_BATCH_SIZE < len(symbols):
                remaining = SCAN_BATCH_INTERVAL_SECONDS - (time.monotonic() - batch_started)
                if remaining > 0:
                    with _SCAN_LOCK:
                        _SCAN_STATE["waiting_for_next_batch_seconds"] = round(remaining)
                    time.sleep(remaining)

        with _SCAN_LOCK:
            rank_order = {"A🟢": 0, "A🟡": 1, "B🟢": 2, "B🟡": 3}
            _SCAN_STATE["matches"].sort(
                key=lambda item: rank_order.get(
                    f"{item.get('timing', 'B')}{'🟢' if item.get('obv_rising') else '🟡'}", 4
                )
            )
            _SCAN_STATE["status"] = "completed"
            _SCAN_STATE["current_symbol"] = None
            _SCAN_STATE["finished_at"] = time.time()
    except Exception as exc:
        with _SCAN_LOCK:
            _SCAN_STATE["status"] = "paused" if isinstance(exc, HTTPException) and exc.status_code == 409 else "failed"
            _SCAN_STATE["current_symbol"] = None
            _SCAN_STATE["fatal_error"] = str(exc.detail if isinstance(exc, HTTPException) else exc)[:240]
            _SCAN_STATE["finished_at"] = time.time()


async def weekly_swing_daily_refresh_loop():
    """Run one incremental history refresh daily after the NSE cash session."""
    ist = ZoneInfo("Asia/Kolkata")
    while True:
        now = datetime.now(ist)
        target = now.replace(hour=15, minute=40, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep(max(1.0, (target - now).total_seconds()))
        if not _is_nse_trading_day(datetime.now(ist).date()):
            continue
        with _SCAN_LOCK:
            if _SCAN_STATE.get("status") == "running":
                continue
            _SCAN_STATE.update({
                "status": "running", "universe": "NIFTY 200", "total": 0, "scanned": 0,
                "failed": 0, "matches": [], "failures": [], "current_symbol": None,
                "started_at": time.time(), "finished_at": None,
                "batch_size": SCAN_BATCH_SIZE, "cache_mode": "persistent_daily_incremental",
                "trigger": "daily_off_market_schedule",
            })
            global _SCAN_THREAD
            _SCAN_THREAD = threading.Thread(target=_run_nifty200_scan, daemon=True, name="weekly-nifty200-daily")
            _SCAN_THREAD.start()


def weekly_bars(rows):
    buckets = {}
    for row in rows or []:
        try:
            d = date.fromisoformat(str(row[0])[:10])
            o,h,l,c,v = map(float,row[1:6])
            if not all(isfinite(x) for x in (o,h,l,c,v)) or min(o,h,l,c)<=0: continue
        except (TypeError,ValueError,IndexError): continue
        key=(d-timedelta(days=d.weekday())).isoformat()
        if key not in buckets: buckets[key]={"week":key,"open":o,"high":h,"low":l,"close":c,"volume":v}
        else:
            b=buckets[key]; b["high"]=max(b["high"],h); b["low"]=min(b["low"],l); b["close"]=c; b["volume"]+=v
    return [buckets[k] for k in sorted(buckets)]


def pivots(values, kind):
    """Return confirmed local swing pivots, ignoring indicator warm-up gaps."""
    out = []
    if len(values) < 3:
        return out
    for i in range(1, len(values) - 1):
        left, current, right = values[i - 1], values[i], values[i + 1]
        if left is None or current is None or right is None:
            continue
        try:
            if not all(isfinite(float(v)) for v in (left, current, right)):
                continue
        except (TypeError, ValueError):
            continue
        if kind == "low" and current <= left and current < right:
            out.append(i)
        elif kind == "high" and current >= left and current > right:
            out.append(i)
    return out


def indicators(bars):
    close=[b["close"] for b in bars]; volume=[max(0,b["volume"]) for b in bars]
    result={k:[] for k in ("cmf","obv","rsi","mid","lower","upper")}
    obv=0.0; gains=[]; losses=[]; avg_gain=None; avg_loss=None
    for i,b in enumerate(bars):
        start=max(0,i-19); denom=sum(volume[start:i+1])
        mfv=sum((((x["close"]-x["low"])-(x["high"]-x["close"]))/(x["high"]-x["low"]) if x["high"]!=x["low"] else 0)*max(0,x["volume"]) for x in bars[start:i+1])
        result["cmf"].append(mfv/denom if denom else 0.0)
        if i:
            obv += volume[i] if close[i]>close[i-1] else -volume[i] if close[i]<close[i-1] else 0
            change=close[i]-close[i-1]
            gains.append(max(change,0)); losses.append(max(-change,0))
        result["obv"].append(obv)
        if i<14:
            result["rsi"].append(None)
        elif i==14:
            avg_gain=mean(gains[:14]); avg_loss=mean(losses[:14])
            result["rsi"].append(100.0 if avg_loss==0 else 100.0-100.0/(1.0+avg_gain/avg_loss))
        else:
            avg_gain=((avg_gain*13)+gains[-1])/14
            avg_loss=((avg_loss*13)+losses[-1])/14
            result["rsi"].append(100.0 if avg_loss==0 else 100.0-100.0/(1.0+avg_gain/avg_loss))
        sample=close[start:i+1]; mid=mean(sample); sd=pstdev(sample) if len(sample)>1 else 0
        result["mid"].append(mid); result["lower"].append(mid-2*sd); result["upper"].append(mid+2*sd)
    return result


def _has_bullish_divergence(values, a, b, c):
    """True when an indicator makes higher lows at three descending price lows."""
    sample = [values[x] for x in (a, b, c)]
    if any(value is None for value in sample):
        return False
    try:
        if not all(isfinite(float(value)) for value in sample):
            return False
    except (TypeError, ValueError):
        return False
    return float(values[b]) >= float(values[a]) and float(values[c]) > float(values[b])


def _rising(series, end, steps=2):
    """True when a numeric indicator has risen across the latest confirmed steps."""
    if end - steps < 0:
        return False
    values = series[end - steps:end + 1]
    if any(value is None for value in values):
        return False
    try:
        values = [float(value) for value in values]
        return all(isfinite(value) for value in values) and all(
            right > left for left, right in zip(values, values[1:])
        )
    except (TypeError, ValueError):
        return False


def analyze(symbol, bars):
    """Weekly RSI scanner: decline -> decline stops -> tight consolidation -> RSI rises.

    Price-fall and consolidation percentages are descriptive fields, not hard filters.
    RSI 45 and fixed 60-day requirements are intentionally not used.
    """
    if len(bars) < 17:
        return {"status": "insufficient_data", "symbol": symbol,
                "weekly_bars": len(bars), "matches": [], "count": 0,
                "message": "At least 17 weekly candles are needed to evaluate RSI rising over two weekly steps."}

    ind = indicators(bars)
    last = len(bars) - 1
    closes = [float(item["close"]) for item in bars]
    lows = [float(item["low"]) for item in bars]
    highs = [float(item["high"]) for item in bars]

    # Describe the recent price fall over up to eight completed weekly bars.
    fall_start = max(0, last - 7)
    reference_high = max(highs[fall_start:last + 1])
    price_fall_pct = ((reference_high - closes[last]) / reference_high * 100.0) if reference_high else 0.0

    # Recent three-week base: the decline must be losing momentum and lows should
    # stop extending down. No arbitrary percentage threshold is used to reject rows.
    base_start = max(0, last - 2)
    base_lows = lows[base_start:last + 1]
    recent_high = max(highs[base_start:last + 1])
    recent_low = min(lows[base_start:last + 1])
    sideways_range_pct = (
        (recent_high - recent_low) / closes[last] * 100.0
        if closes[last] > 0 else 0.0
    )
    prior_start = max(0, last - 7)
    prior_range = max(highs[prior_start:last + 1]) - min(lows[prior_start:last + 1])
    tight_sideways = (
        len(base_lows) >= 3
        and lows[last] >= min(lows[last - 2:last]) * 0.995
        and (recent_high - recent_low) <= prior_range * 0.60
    )
    fall_stopped = len(base_lows) >= 3 and lows[last] >= min(lows[last - 2:last]) * 0.995
    price_falling = last >= 4 and closes[last - 1] < closes[max(0, last - 5)]
    rsi_rising = _rising(ind["rsi"], last, 2)
    obv_rising = _rising(ind["obv"], last, 2)

    # "RSI Before Sideways" means RSI had already turned up before the last
    # three weekly candles that form the candidate consolidation.
    rsi_before_sideways = (
        last >= 5 and _rising(ind["rsi"], last - 3, 2)
    )
    sma20 = mean(closes[max(0, last - 19):last + 1]) if last >= 19 else None
    sma20_near = (
        abs(closes[last] - sma20) <= max(highs[last] - lows[last], 1e-9)
        if sma20 is not None else None
    )
    price_phase = (
        "Tight Sideways" if tight_sideways
        else "Fall Stop" if fall_stopped
        else "Fall" if price_falling
        else "Other"
    )
    special_timing = "RSI Before Sideways" if rsi_before_sideways else "RSI During/After Sideways"
    timing = "A" if rsi_before_sideways else "B"
    signal = "PASS" if price_falling and fall_stopped and tight_sideways and rsi_rising else "WATCH"

    # Only genuine setups are returned; non-matches are not invented as opportunities.
    matches = []
    if signal == "PASS":
        confirmed = ["RSI"] + (["OBV"] if obv_rising else [])
        matches.append({
            "symbol": symbol, "status": "setup_detected",
            "strength": "STRONG" if obv_rising else "QUALIFIED",
            "indicators_confirmed": confirmed,
            "lows": [{"week": bars[i]["week"], "low": lows[i], "close": closes[i]}
                     for i in range(max(0, last - 2), last + 1)],
            "intervening_highs": [],
            "third_low_lower_band": ind["lower"][last],
            "indicator_values_at_third_low": {
                "RSI": ind["rsi"][last], "OBV": ind["obv"][last]
            },
            "checked_at_week": bars[last]["week"],
            "price": closes[last],
            "rsi": ind["rsi"][last],
            "rsi_rising": rsi_rising,
            "obv_rising": obv_rising,
            "price_fall_pct": round(price_fall_pct, 2),
            "price_phase": price_phase,
            "sideways_range_pct": round(sideways_range_pct, 2),
            "rsi_before_sideways": rsi_before_sideways,
            "sma20_near": sma20_near,
            "sma20": sma20,
            "special_timing": special_timing,
            "timing": timing,
            "timeframe": "Weekly",
            "signal": signal,
        })

    return {
        "status": "success", "symbol": symbol, "weekly_bars": len(bars),
        "count": len(matches), "matches": matches,
        "data_source": "Angel One daily candles aggregated to weekly",
        "orders_placed": False,
    }

@router.get("/scan")
def scan(symbol:str=Query(...,min_length=1,max_length=40),exchange:str=Query("NSE",pattern="^NSE$")):
    from app.market_data.client import MarketDataClient
    from app.market_data.historical import HistoricalDataClient
    from app.market_data.instruments import InstrumentMaster
    ticker=symbol.strip().upper()
    if exchange != "NSE":
        raise HTTPException(status_code=422, detail="Weekly Swing Scanner supports NSE only")
    if _nse_market_is_open_now():
        raise HTTPException(status_code=409, detail="Historical scanner downloads are off-market only; try after 15:30 IST.")
    exchange = "NSE"
    master = InstrumentMaster()
    instrument = master.get_instrument(ticker, exchange)
    if not instrument and not ticker.endswith("-EQ"):
        instrument = master.get_instrument(f"{ticker}-EQ", exchange)
    if not instrument or not instrument.get("token"):
        raise HTTPException(status_code=404, detail=f"Instrument not found: {exchange} {ticker}")
    end = _nse_today()
    try:
        historical = HistoricalDataClient(MarketDataClient())
        return _scan_one_symbol(ticker, master, historical, end)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Historical scanner data unavailable from configured provider") from exc


@router.post("/scan-all/start")
def start_nifty200_scan():
    """Start the cached NIFTY 200 scan outside regular NSE market hours."""
    global _SCAN_THREAD
    if _nse_market_is_open_now():
        raise HTTPException(status_code=409, detail="Historical NIFTY 200 scan is off-market only; try after 15:30 IST.")
    with _SCAN_LOCK:
        if _SCAN_STATE.get("status") == "running":
            return {**_SCAN_STATE, "message": "NIFTY 200 scan already running"}
        _SCAN_STATE.update({
            "status": "running", "universe": "NIFTY 200", "total": 0, "scanned": 0,
            "failed": 0, "matches": [], "failures": [], "current_symbol": None,
            "started_at": time.time(), "finished_at": None,
        })
        _SCAN_THREAD = threading.Thread(target=_run_nifty200_scan, daemon=True, name="weekly-nifty200-scan")
        _SCAN_THREAD.start()
        return {"status": "running", "universe": "NIFTY 200", "message": "Background scan started", "orders_placed": False}


@router.get("/scan-all/status")
def nifty200_scan_status():
    with _SCAN_LOCK:
        return {**_SCAN_STATE, "orders_placed": False}
