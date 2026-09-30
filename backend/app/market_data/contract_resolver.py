"""Deterministic, provider-neutral resolver for active derivative contracts."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date,datetime
from typing import Any,Iterable

@dataclass(frozen=True)
class ContractCandidate:
    token:str; symbol:str; exchange:str; segment:str; instrument_type:str
    underlying:str|None=None; expiry:str|None=None; strike:float|None=None
    option_type:str|None=None; lot_size:int|None=None; tick_size:float|None=None
    raw:dict[str,Any]|None=None

def _text(row,*names):
    for n in names:
        v=row.get(n)
        if v is not None and str(v).strip(): return str(v).strip()
    return ""
def _expiry_value(value):
    if value is None or not str(value).strip(): return None
    for fmt in ("%d%b%Y","%d-%b-%Y","%Y-%m-%d","%d/%m/%Y","%d%b%y"):
        try: return datetime.strptime(str(value).strip().upper(),fmt).date()
        except ValueError: pass
    return None
def _kind(row):
    raw=_text(row,"instrumenttype","instrument_type","instrumentType").upper()
    # OPTFUT/OPTIDX contain both markers; option classification must win.
    if "OPT" in raw: return "option"
    if "FUT" in raw: return "future"
    if "COM" in _text(row,"exch_seg","segment","exchange_segment").upper(): return "commodity"
    if raw in {"INDEX","INDICES"}: return "index"
    return "equity"

class DynamicContractResolver:
    """Resolve CURRENT/NEAR futures and ATM±N options without hard-coded tokens."""
    def __init__(self,rows:Iterable[dict[str,Any]],as_of:date|None=None):
        self.as_of=as_of or date.today(); self.rows=tuple(dict(r) for r in rows)
    def candidates(self,underlying=None,kind=None):
        target=str(underlying).strip().upper() if underlying else None; out=[]
        for row in self.rows:
            k=_kind(row)
            if kind and k!=kind: continue
            ru=_text(row,"name","underlying","symbol").upper(); symbol=_text(row,"symbol","tradingsymbol","trading_symbol")
            if target and target not in {ru,symbol.upper()}: continue
            token=_text(row,"token","symboltoken","symbol_token")
            if not token: continue
            out.append(ContractCandidate(token,symbol or token,_text(row,"exchange","exch_seg","exchange_segment"),_text(row,"exch_seg","segment","exchange_segment"),k,ru or None,_text(row,"expiry") or None,float(row["strike"]) if row.get("strike") not in (None,"") else None,_text(row,"optiontype","option_type").upper() or None,int(row["lotsize"]) if str(row.get("lotsize","")).strip() else None,float(row["tick_size"]) if row.get("tick_size") not in (None,"") else None,row))
        return tuple(out)
    def resolve_futures(self,underlying,limit=2):
        if isinstance(limit,bool) or not isinstance(limit,int) or limit<1: raise ValueError("limit must be a positive integer")
        eligible=[c for c in self.candidates(underlying,"future") if (d:=_expiry_value(c.expiry)) and d>=self.as_of]
        eligible.sort(key=lambda c:(_expiry_value(c.expiry),c.symbol,c.token)); seen=set(); out=[]
        for c in eligible:
            d=_expiry_value(c.expiry)
            if d in seen: continue
            seen.add(d); out.append(c)
            if len(out)>=limit: break
        return tuple(out)
    def resolve_options(self,underlying,spot,strike_count=10,expiry_rank=0):
        if isinstance(strike_count,bool) or strike_count<0: raise ValueError("strike_count must be non-negative")
        if isinstance(expiry_rank,bool) or expiry_rank<0: raise ValueError("expiry_rank must be non-negative")
        eligible=[c for c in self.candidates(underlying,"option") if c.strike is not None and c.option_type in {"CE","PE"} and (d:=_expiry_value(c.expiry)) and d>=self.as_of]
        expiries=sorted({_expiry_value(c.expiry) for c in eligible})
        if expiry_rank>=len(expiries): return ()
        expiry=expiries[expiry_rank]; strikes=sorted({c.strike for c in eligible if _expiry_value(c.expiry)==expiry})
        if not strikes: return ()
        ai=min(range(len(strikes)),key=lambda i:(abs(strikes[i]-spot),strikes[i]))
        wanted=set(strikes[max(0,ai-strike_count):min(len(strikes),ai+strike_count+1)])
        out=[c for c in eligible if _expiry_value(c.expiry)==expiry and c.strike in wanted]
        out.sort(key=lambda c:(c.strike,c.option_type or "",c.symbol,c.token)); return tuple(out)
