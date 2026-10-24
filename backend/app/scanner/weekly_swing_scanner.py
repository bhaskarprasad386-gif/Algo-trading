"""Weekly three-swing scanner; real daily bars are aggregated to weekly bars."""
from datetime import date, timedelta
from statistics import mean, pstdev
from math import isfinite
import csv
import io
import threading
import time
import requests
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/v1/scanner/weekly-swing", tags=["Weekly Swing Scanner"])

NIFTY200_CONSTITUENTS_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv"
_SCAN_LOCK = threading.Lock()
_SCAN_STATE = {
    "status": "idle", "universe": "NIFTY 200", "total": 200, "scanned": 0,
    "failed": 0, "matches": [], "failures": [], "started_at": None, "finished_at": None,
}
_SCAN_THREAD = None


def _nifty200_symbols():
    """Read the current NIFTY 200 constituents from the official index CSV."""
    response = requests.get(
        NIFTY200_CONSTITUENTS_URL,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.niftyindices.com/"},
    )
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text.lstrip(chr(65279))))
    symbols = []
    for row in reader:
        symbol = (row.get("Symbol") or row.get("symbol") or "").strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    if len(symbols) < 180:
        raise ValueError(f"Official NIFTY 200 constituent list incomplete: {len(symbols)} symbols")
    return symbols


def _scan_one_symbol(ticker, master, historical, start, end):
    instrument = master.get_instrument(ticker, "NSE")
    if not instrument and not ticker.endswith("-EQ"):
        instrument = master.get_instrument(f"{ticker}-EQ", "NSE")
    if not instrument or not instrument.get("token"):
        raise ValueError("Instrument/token not found in Angel One master")
    rows = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=300), end)
        response = historical.get_candles(
            exchange="NSE", symboltoken=str(instrument["token"]), interval="ONE_DAY",
            from_date=f"{cursor} 09:15", to_date=f"{chunk_end} 15:30",
        )
        if not isinstance(response, dict) or not response.get("status") or not isinstance(response.get("data"), list):
            raise ValueError("Historical candle response invalid")
        rows.extend(response["data"])
        cursor = chunk_end + timedelta(days=1)
    result = analyze(ticker, weekly_bars(rows))
    if result.get("status") == "insufficient_data":
        raise ValueError("Insufficient weekly history")
    return result


def _run_nifty200_scan():
    global _SCAN_STATE
    from app.market_data.client import MarketDataClient
    from app.market_data.historical import HistoricalDataClient
    from app.market_data.instruments import InstrumentMaster

    with _SCAN_LOCK:
        _SCAN_STATE = {
            "status": "running", "universe": "NIFTY 200", "total": 0, "scanned": 0,
            "failed": 0, "matches": [], "failures": [], "started_at": time.time(), "finished_at": None,
        }
    try:
        symbols = _nifty200_symbols()
        master = InstrumentMaster()
        historical = HistoricalDataClient(MarketDataClient())
        end = date.today()
        start = end - timedelta(days=365 * 5)
        with _SCAN_LOCK:
            _SCAN_STATE["total"] = len(symbols)
        for ticker in symbols:
            try:
                result = _scan_one_symbol(ticker, master, historical, start, end)
                if result.get("matches"):
                    with _SCAN_LOCK:
                        _SCAN_STATE["matches"].extend(result["matches"])
            except Exception as exc:
                with _SCAN_LOCK:
                    _SCAN_STATE["failed"] += 1
                    _SCAN_STATE["failures"].append({"symbol": ticker, "reason": str(exc)[:180]})
            finally:
                with _SCAN_LOCK:
                    _SCAN_STATE["scanned"] += 1
        with _SCAN_LOCK:
            _SCAN_STATE["status"] = "completed"
            _SCAN_STATE["finished_at"] = time.time()
    except Exception as exc:
        with _SCAN_LOCK:
            _SCAN_STATE["status"] = "failed"
            _SCAN_STATE["fatal_error"] = str(exc)[:240]
            _SCAN_STATE["finished_at"] = time.time()


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
    obv=0.0; gains=[]; losses=[]
    for i,b in enumerate(bars):
        start=max(0,i-19); denom=sum(volume[start:i+1])
        mfv=sum((((x["close"]-x["low"])-(x["high"]-x["close"]))/(x["high"]-x["low"]) if x["high"]!=x["low"] else 0)*max(0,x["volume"]) for x in bars[start:i+1])
        result["cmf"].append(mfv/denom if denom else 0.0)
        if i: 
            obv += volume[i] if close[i]>close[i-1] else -volume[i] if close[i]<close[i-1] else 0
            change=close[i]-close[i-1]; gains.append(max(change,0)); losses.append(max(-change,0))
        result["obv"].append(obv)
        if i<14: result["rsi"].append(None)
        else:
            ag=mean(gains[i-14:i]) if len(gains)>=14 else 0
            al=mean(losses[i-14:i]) if len(losses)>=14 else 0
            result["rsi"].append(100 if al==0 else 100-100/(1+ag/al))
        sample=close[start:i+1]; mid=mean(sample); sd=pstdev(sample) if len(sample)>1 else 0
        result["mid"].append(mid); result["lower"].append(mid-2*sd); result["upper"].append(mid+2*sd)
    return result

def analyze(symbol,bars):
    if len(bars)<25:return {"status":"insufficient_data","symbol":symbol,"weekly_bars":len(bars),"matches":[]}
    ind=indicators(bars); lows=pivots([b["low"] for b in bars],"low"); highs=pivots([b["high"] for b in bars],"high")
    ih={n:pivots(ind[n],"high") for n in ("cmf","obv","rsi")}; matches=[]
    for k in range(2,len(lows)):
        a,b,c=lows[k-2:k+1]
        if not (bars[b]["low"]<bars[a]["low"] and bars[c]["low"]<bars[b]["low"]):continue
        h1=[x for x in highs if a<x<b]; h2=[x for x in highs if b<x<c]
        if not h1 or not h2:continue
        h1,h2=h1[-1],h2[-1]
        # Adaptive shallow-break rule: the close may sit slightly below the prior
        # swing low, but not farther than the current week's own full range. This
        # avoids a fixed point/percentage threshold across differently priced symbols.
        if not all(
            bars[x]["close"] >= bars[p]["low"] - max(bars[x]["high"] - bars[x]["low"], 1e-9)
            for p, x in ((a, b), (b, c))
        ):
            continue
        if not all(bars[x]["low"]<bars[p]["low"] and bars[x]["close"]>bars[x]["low"] for p,x in ((a,b),(b,c))):continue
        rng=max(bars[c]["high"]-bars[c]["low"],1e-9)
        if abs(bars[c]["low"]-ind["lower"][c])>rng:continue
        if not all(abs(bars[x]["high"]-ind["mid"][x])<=max(bars[x]["high"]-bars[x]["low"],1e-9) and bars[x]["high"]<ind["upper"][x] for x in (h1,h2)):continue
        passed=[]
        for n in ("cmf","obv","rsi"):
            vals=ind[n]
            if any(vals[x] is None for x in (a,b,c)):continue
            interval=all(v is not None and v>=vals[a] for v in vals[a:b+1])
            prior=[x for x in ih[n] if x<c and x<b][-2:]
            if interval and len(prior)==2 and all(vals[c]>vals[x] for x in prior):passed.append(n.upper())
        if passed:
            matches.append({"symbol":symbol,"status":"setup_detected","strength":"STRONGEST" if len(passed)==3 else "STRONG" if len(passed)==2 else "QUALIFIED","indicators_confirmed":passed,"lows":[{"week":bars[x]["week"],"low":bars[x]["low"],"close":bars[x]["close"]} for x in (a,b,c)],"intervening_highs":[{"week":bars[x]["week"],"high":bars[x]["high"]} for x in (h1,h2)],"third_low_lower_band":ind["lower"][c],"indicator_values_at_third_low":{n:ind[n.lower()][c] for n in ("CMF","OBV","RSI")},"checked_at_week":bars[-1]["week"]})
    return {"status":"success","symbol":symbol,"weekly_bars":len(bars),"count":len(matches),"matches":matches[-5:],"data_source":"Angel One daily candles aggregated to weekly","orders_placed":False}

@router.get("/scan")
def scan(symbol:str=Query(...,min_length=1,max_length=40),exchange:str=Query("NSE",pattern="^(NSE|BSE)$")):
    from app.market_data.client import MarketDataClient
    from app.market_data.historical import HistoricalDataClient
    from app.market_data.instruments import InstrumentMaster
    ticker=symbol.strip().upper()
    master = InstrumentMaster()
    instrument = master.get_instrument(ticker, exchange)
    # Angel One's cash-equity master commonly stores symbols with the -EQ suffix.
    # Accept friendly inputs such as WIPRO while still resolving via the real master.
    if not instrument and not ticker.endswith("-EQ"):
        instrument = master.get_instrument(f"{ticker}-EQ", exchange)
    if not instrument or not instrument.get("token"):
        raise HTTPException(status_code=404, detail=f"Instrument not found: {exchange} {ticker}")
    end = date.today()
    start = end - timedelta(days=365 * 5)
    try:
        # Angel One daily-candle history has a bounded per-request date range.
        # Fetch sequential chunks and fail closed if any provider request fails.
        historical = HistoricalDataClient(MarketDataClient())
        rows = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=300), end)
            response = historical.get_candles(
                exchange=exchange,
                symboltoken=str(instrument["token"]),
                interval="ONE_DAY",
                from_date=f"{cursor} 09:15",
                to_date=f"{chunk_end} 15:30",
            )
            if not isinstance(response, dict) or not response.get("status") or not isinstance(response.get("data"), list):
                raise ValueError("Historical provider returned no valid candle data")
            rows.extend(response["data"])
            cursor = chunk_end + timedelta(days=1)
        return analyze(ticker, weekly_bars(rows))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Historical scanner data unavailable from configured provider") from exc


@router.post("/scan-all/start")
def start_nifty200_scan():
    """Start a real, background scan across the current official NIFTY 200 universe."""
    global _SCAN_THREAD
    with _SCAN_LOCK:
        if _SCAN_STATE.get("status") == "running":
            return {**_SCAN_STATE, "message": "NIFTY 200 scan already running"}
        _SCAN_THREAD = threading.Thread(target=_run_nifty200_scan, daemon=True, name="weekly-nifty200-scan")
        _SCAN_THREAD.start()
        return {"status": "starting", "universe": "NIFTY 200", "message": "Background scan started", "orders_placed": False}


@router.get("/scan-all/status")
def nifty200_scan_status():
    with _SCAN_LOCK:
        return {**_SCAN_STATE, "orders_placed": False}
