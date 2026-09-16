"""Executable multi-leg arbitrage primitives for liquid F&O backtests."""
from __future__ import annotations
from dataclasses import dataclass
from math import exp, isfinite
from typing import Literal

ArbitrageKind = Literal["BOX", "SYNTHETIC_CASH_CARRY", "CASH_CARRY"]

@dataclass(frozen=True)
class OptionQuote:
    timestamp_ns:int; underlying:str; expiry:int; strike:float; call_bid:float; call_ask:float; put_bid:float; put_ask:float; lot_size:int=1; instrument_class:Literal["STOCK","INDEX"]="STOCK"; volume:int=0; oi:int=0

@dataclass(frozen=True)
class FutureQuote:
    timestamp_ns:int; underlying:str; expiry:int; bid:float; ask:float; lot_size:int=1; instrument_class:Literal["STOCK","INDEX"]="STOCK"; volume:int=0; oi:int=0

@dataclass(frozen=True)
class LiquidityPolicy:
    min_option_volume:int=0; min_option_oi:int=0; max_spread_pct:float=100.0; min_future_volume:int=0; min_future_oi:int=0
    @staticmethod
    def _accepts_quote(*,volume:int,oi:int,bid:float,ask:float,min_volume:int,min_oi:int,max_spread_pct:float)->bool:
        if any(isinstance(v,bool) for v in (volume,oi,min_volume,min_oi)): return False
        if volume<min_volume or oi<min_oi or not all(isfinite(float(v)) for v in (bid,ask,max_spread_pct)): return False
        if bid<=0 or ask<bid or max_spread_pct<0: return False
        return ((ask-bid)/bid)*100.0<=max_spread_pct
    def accepts(self,*,volume:int,oi:int,bid:float,ask:float)->bool:
        return self._accepts_quote(volume=volume,oi=oi,bid=bid,ask=ask,min_volume=self.min_option_volume,min_oi=self.min_option_oi,max_spread_pct=self.max_spread_pct)
    def accepts_future(self,future:FutureQuote)->bool:
        return self._accepts_quote(volume=future.volume,oi=future.oi,bid=future.bid,ask=future.ask,min_volume=self.min_future_volume,min_oi=self.min_future_oi,max_spread_pct=self.max_spread_pct)

@dataclass(frozen=True)
class ArbitrageOpportunity:
    kind:ArbitrageKind; direction:Literal["LONG","SHORT"]; timestamp_ns:int; underlying:str; expiry:int; strike_low:float|None; strike_high:float|None; executable_edge:float; edge_per_lot:float; gross_pnl:float; width_or_notional:float

def _validate_option_quote(quote:OptionQuote)->None:
    if isinstance(quote.timestamp_ns,bool) or not isinstance(quote.timestamp_ns,int) or quote.timestamp_ns<0: raise ValueError("option timestamp_ns must be a non-negative integer")
    if not isinstance(quote.underlying,str) or not quote.underlying.strip(): raise ValueError("option underlying is required")
    if isinstance(quote.expiry,bool) or not isinstance(quote.expiry,int) or quote.expiry<0: raise ValueError("option expiry must be a non-negative integer")
    values=(quote.strike,quote.call_bid,quote.call_ask,quote.put_bid,quote.put_ask)
    if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and isfinite(float(v)) for v in values): raise ValueError("option prices and strike must be finite")
    if quote.strike<=0: raise ValueError("option strike must be positive")
    if quote.call_bid<0 or quote.put_bid<0 or quote.call_ask<=0 or quote.put_ask<=0 or quote.call_ask<quote.call_bid or quote.put_ask<quote.put_bid: raise ValueError("invalid executable option bid/ask")
    if quote.instrument_class not in {"STOCK","INDEX"}: raise ValueError("invalid option instrument_class")
    if type(quote.lot_size) is not int or quote.lot_size<=0: raise ValueError("option lot_size must be a positive integer")
    if type(quote.volume) is not int or quote.volume<0 or type(quote.oi) is not int or quote.oi<0: raise ValueError("option volume and oi must be non-negative integers")

def _validate_future_quote(future:FutureQuote)->None:
    if isinstance(future.timestamp_ns,bool) or not isinstance(future.timestamp_ns,int) or future.timestamp_ns<0: raise ValueError("future timestamp_ns must be a non-negative integer")
    if not isinstance(future.underlying,str) or not future.underlying.strip(): raise ValueError("future underlying is required")
    if isinstance(future.expiry,bool) or not isinstance(future.expiry,int) or future.expiry<0: raise ValueError("future expiry must be a non-negative integer")
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(float(v)) for v in (future.bid,future.ask)): raise ValueError("future prices must be finite")
    if future.bid<0 or future.ask<=0 or future.ask<future.bid: raise ValueError("future ask price must be positive and at least bid price")
    if future.instrument_class not in {"STOCK","INDEX"}: raise ValueError("invalid future instrument_class")
    if type(future.lot_size) is not int or future.lot_size<=0: raise ValueError("future lot_size must be a positive integer")
    if type(future.volume) is not int or future.volume<0 or type(future.oi) is not int or future.oi<0: raise ValueError("future volume and oi must be non-negative integers")

def _validate_direction(direction:str)->None:
    if direction not in ("LONG","SHORT"): raise ValueError("direction must be LONG or SHORT")

def _validate_fees(value:float)->None:
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(float(value)) or value<0: raise ValueError("fees_per_unit must be finite and non-negative")

class BoxSpreadBacktester:
    @staticmethod
    def evaluate(low:OptionQuote,high:OptionQuote,*,direction:Literal["LONG","SHORT"]="LONG",fees_per_unit:float=0.0)->ArbitrageOpportunity|None:
        _validate_direction(direction); _validate_option_quote(low); _validate_option_quote(high); _validate_fees(fees_per_unit)
        if low.underlying!=high.underlying or low.expiry!=high.expiry or low.instrument_class!=high.instrument_class: raise ValueError("box legs must share underlying, expiry and instrument class")
        if not low.strike<high.strike: raise ValueError("low strike must be below high strike")
        if low.timestamp_ns!=high.timestamp_ns: raise ValueError("box legs must share timestamp")
        if low.lot_size!=high.lot_size: raise ValueError("box legs must share lot size")
        width=high.strike-low.strike
        if direction=="LONG": debit=low.call_ask+high.put_ask-high.call_bid-low.put_bid; edge=width-debit-fees_per_unit
        else: credit=low.call_bid+high.put_bid-high.call_ask-low.put_ask; edge=credit-width-fees_per_unit
        if edge<=0:return None
        pnl=edge*low.lot_size
        return ArbitrageOpportunity("BOX",direction,low.timestamp_ns,low.underlying,low.expiry,low.strike,high.strike,edge,pnl,pnl,width)

class SyntheticCashCarryBacktester:
    @staticmethod
    def evaluate(option:OptionQuote,future:FutureQuote,*,rate:float=0.0,time_to_expiry_years:float,fees_per_unit:float=0.0,direction:Literal["LONG","SHORT"]="LONG",liquidity:LiquidityPolicy|None=None)->ArbitrageOpportunity|None:
        _validate_direction(direction); _validate_option_quote(option); _validate_future_quote(future)
        for value,name in ((rate,"rate"),(time_to_expiry_years,"time_to_expiry_years"),(fees_per_unit,"fees_per_unit")):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(float(value)): raise ValueError(f"{name} must be finite")
        if time_to_expiry_years<0: raise ValueError("time_to_expiry_years must be non-negative")
        if fees_per_unit<0: raise ValueError("fees_per_unit must be non-negative")
        if option.underlying!=future.underlying or option.expiry!=future.expiry or option.instrument_class!=future.instrument_class: raise ValueError("synthetic legs must share underlying, expiry and instrument class")
        if option.timestamp_ns!=future.timestamp_ns: raise ValueError("synthetic legs must share timestamp")
        if option.lot_size!=future.lot_size: raise ValueError("synthetic legs must share lot size")
        if liquidity is not None:
            if not liquidity.accepts(volume=option.volume,oi=option.oi,bid=option.call_bid,ask=option.call_ask): return None
            if not liquidity.accepts(volume=option.volume,oi=option.oi,bid=option.put_bid,ask=option.put_ask): return None
            if not liquidity.accepts_future(future): return None
        carry=exp(rate*time_to_expiry_years)
        synthetic_buy=option.strike+(option.call_ask-option.put_bid)*carry
        synthetic_sell=option.strike+(option.call_bid-option.put_ask)*carry
        edge=(future.bid-synthetic_buy-fees_per_unit) if direction=="LONG" else (synthetic_sell-future.ask-fees_per_unit)
        if edge<=0:return None
        pnl=edge*future.lot_size
        return ArbitrageOpportunity("SYNTHETIC_CASH_CARRY",direction,future.timestamp_ns,future.underlying,future.expiry,None,None,edge,pnl,pnl,future.bid if direction=="LONG" else future.ask)

__all__=["ArbitrageOpportunity","BoxSpreadBacktester","FutureQuote","LiquidityPolicy","OptionQuote","SyntheticCashCarryBacktester"]
