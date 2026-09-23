"""Historical arbitrage chain/universe selection with strict identity rules."""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Sequence

SUPPORTED_VENUES=frozenset({"NSE","BSE","MCX","COMMODITY"})
SUPPORTED_INSTRUMENT_CLASSES=frozenset({"STOCK","INDEX","COMMODITY"})

@dataclass(frozen=True)
class ChainContract:
    timestamp_ns:int; venue:str; underlying:str; instrument_class:str; expiry:int; strike:float; option_type:str; lot_size:int=1; volume:float=0.0; oi:float=0.0; bid:float=0.0; ask:float=0.0
    def __post_init__(self)->None:
        if isinstance(self.timestamp_ns,bool) or not isinstance(self.timestamp_ns,int) or self.timestamp_ns<0: raise ValueError("timestamp_ns must be a non-negative integer")
        for value,name in ((self.venue,"venue"),(self.underlying,"underlying"),(self.instrument_class,"instrument_class"),(self.option_type,"option_type")):
            if not isinstance(value,str) or not value.strip(): raise ValueError(f"{name} is required")
        if self.venue not in SUPPORTED_VENUES: raise ValueError(f"unsupported venue: {self.venue}")
        if self.instrument_class not in SUPPORTED_INSTRUMENT_CLASSES: raise ValueError(f"unsupported instrument class: {self.instrument_class}")
        if self.option_type not in {"CE","PE"}: raise ValueError("option_type must be CE or PE")
        if type(self.lot_size) is not int or self.lot_size<=0: raise ValueError("lot_size must be a positive integer")
        for value,name in ((self.strike,"strike"),(self.volume,"volume"),(self.oi,"oi"),(self.bid,"bid"),(self.ask,"ask")):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(float(value)): raise ValueError(f"{name} must be finite")
        if self.strike<0 or self.volume<0 or self.oi<0 or self.bid<0 or self.ask<self.bid: raise ValueError("invalid historical contract fields")

@dataclass(frozen=True)
class SelectedPair:
    low:ChainContract; high:ChainContract; low_position:int; high_position:int

def _validate_same_snapshot(contracts:Sequence[ChainContract],*,timestamp_ns:int|None=None,underlying:str|None=None,expiry:int|None=None,allow_expiry_variation:bool=False)->None:
    if not contracts:return
    first=contracts[0]; expected_timestamp=first.timestamp_ns if timestamp_ns is None else timestamp_ns; expected_underlying=first.underlying if underlying is None else underlying
    for contract in contracts:
        if contract.timestamp_ns!=expected_timestamp or contract.underlying!=expected_underlying or contract.venue!=first.venue or contract.instrument_class!=first.instrument_class:
            raise ValueError("historical chain contracts must share timestamp, underlying, venue and instrument class")
        if not allow_expiry_variation and contract.expiry!=(first.expiry if expiry is None else expiry): raise ValueError("historical chain contracts must share expiry")

def _atm_index(strikes:Sequence[float],atm:float|None)->int:
    if not strikes: raise ValueError("historical chain is empty")
    if atm is None or not isfinite(float(atm)): raise ValueError("historical ATM is required and must be finite")
    return min(range(len(strikes)),key=lambda i:(abs(strikes[i]-atm),strikes[i]))

def _select_side_positions(contracts:Sequence[ChainContract],*,atm:float,positions_below:int,positions_above:int,exclude_between:int=0)->tuple[ChainContract,...]:
    if positions_below<0 or positions_above<0 or exclude_between<0: raise ValueError("position counts must be non-negative")
    if not contracts:return ()
    _validate_same_snapshot(contracts); strikes=sorted({c.strike for c in contracts}); ai=_atm_index(strikes,atm)
    lower=range(max(0,ai-positions_below),max(0,ai-exclude_between)); upper=range(min(len(strikes),ai+exclude_between+1),min(len(strikes),ai+positions_above+1)); wanted=set(lower)|set(upper)
    return tuple(sorted((c for c in contracts if strikes.index(c.strike) in wanted),key=lambda c:(c.strike,c.option_type)))

def _require_complete_positions(selected:Sequence[ChainContract],*,expected:int,message:str)->None:
    if len({c.strike for c in selected})!=expected: raise ValueError(message)

def _require_exact_side_positions(contracts:Sequence[ChainContract],*,atm:float,below:tuple[int,...],above:tuple[int,...],message:str)->None:
    _validate_same_snapshot(contracts); strikes=sorted({c.strike for c in contracts}); ai=_atm_index(strikes,atm); required={ai+offset for offset in below}|{ai+offset for offset in above}
    if any(index<0 or index>=len(strikes) for index in required): raise ValueError(message)

def select_box_stock(contracts:Sequence[ChainContract],*,atm:float)->tuple[ChainContract,...]:
    if not contracts or contracts[0].instrument_class!="STOCK": raise ValueError("Box stock selection requires stock option contracts")
    _require_exact_side_positions(contracts,atm=atm,below=(-1,-2,-3,-4,-5),above=(1,2,3,4,5),message="historical Box stock chain is incomplete: five positions are required on each side")
    selected=_select_side_positions(contracts,atm=atm,positions_below=5,positions_above=5); _require_complete_positions(selected,expected=10,message="historical Box stock chain is incomplete: five positions are required on each side"); return selected

def select_box_index(contracts:Sequence[ChainContract],*,atm:float)->tuple[ChainContract,...]:
    if not contracts or contracts[0].instrument_class!="INDEX": raise ValueError("Box index selection requires index option contracts")
    below=tuple(range(-15,-2)); above=tuple(range(3,16)); _require_exact_side_positions(contracts,atm=atm,below=below,above=above,message="historical Box index chain is incomplete: positions 3 through 15 are required on both sides")
    selected=_select_side_positions(contracts,atm=atm,positions_below=15,positions_above=15,exclude_between=2); _require_complete_positions(selected,expected=26,message="historical Box index chain is incomplete: positions 3 through 15 are required on both sides"); return selected

def select_synthetic_stock(contracts:Sequence[ChainContract],*,atm:float)->tuple[ChainContract,...]:
    if not contracts or contracts[0].instrument_class!="STOCK": raise ValueError("Synthetic stock selection requires stock option contracts")
    return _select_side_positions(contracts,atm=atm,positions_below=5,positions_above=5)

def select_synthetic_index(contracts:Sequence[ChainContract],*,atm:float)->tuple[ChainContract,...]:
    if not contracts or contracts[0].instrument_class!="INDEX": raise ValueError("Synthetic index selection requires index option contracts")
    return _select_side_positions(contracts,atm=atm,positions_below=15,positions_above=15)

def select_calendar_expiries(contracts:Sequence[ChainContract])->tuple[tuple[int,int],...]:
    if not contracts:return ()
    _validate_same_snapshot(contracts,allow_expiry_variation=True); expiries=sorted({c.expiry for c in contracts}); return tuple((near,far) for i,near in enumerate(expiries) for far in expiries[i+1:])

def pair_by_strike(contracts:Sequence[ChainContract],*,expiry:int,option_type:str)->tuple[SelectedPair,...]:
    if option_type not in {"CE","PE"}: raise ValueError("option_type must be CE or PE")
    filtered=[c for c in contracts if c.expiry==expiry and c.option_type==option_type]
    if not filtered:return ()
    _validate_same_snapshot(filtered,expiry=expiry); by_strike={}
    for c in filtered:
        if c.strike in by_strike: raise ValueError(f"duplicate strike in historical chain: {c.strike}")
        by_strike[c.strike]=c
    strikes=sorted(by_strike); return tuple(SelectedPair(by_strike[lo],by_strike[hi],i,j) for i,lo in enumerate(strikes) for j,hi in enumerate(strikes) if i<j)

__all__=["SUPPORTED_VENUES","SUPPORTED_INSTRUMENT_CLASSES","ChainContract","SelectedPair","select_box_stock","select_box_index","select_synthetic_stock","select_synthetic_index","select_calendar_expiries","pair_by_strike"]
