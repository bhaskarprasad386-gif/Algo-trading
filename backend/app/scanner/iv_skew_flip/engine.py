"""Pure, isolated analytics for the IV Skew / IV Flip / Gamma Blast scanner.

This module intentionally has no broker, order-routing, Box Spread, or Synthetic
Runner dependencies. IV is derived from option marks because the canonical
Angel One market-data record carries LTP/depth/OI, not an exchange IV field.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import erf, exp, isfinite, log, sqrt
from typing import Iterable, Mapping


@dataclass(frozen=True)
class OptionQuote:
    underlying: str
    expiry: str
    strike: float
    option_type: str
    timestamp_ns: int
    ltp: float | None
    bid: float | None = None
    ask: float | None = None
    oi: int | None = None
    volume: int | None = None
    token: str | None = None

    @property
    def mark(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            if isfinite(self.bid) and isfinite(self.ask) and 0 < self.bid <= self.ask:
                return (self.bid + self.ask) / 2.0
        if self.ltp is not None and isfinite(self.ltp) and self.ltp > 0:
            return self.ltp
        return None


@dataclass(frozen=True)
class Signal:
    strategy: str
    direction: str | None
    value: float | None
    threshold: float | None
    status: str
    reason: str


@dataclass(frozen=True)
class ScannerConfig:
    symbol: str
    expiry_type: str
    bullish_skew_threshold: float
    bearish_skew_threshold: float
    max_quote_age_seconds: float = 5.0
    risk_free_rate: float = 0.065
    dividend_yield: float = 0.0
    min_flip_gap_iv_points: float = 0.15
    gamma_min_oi_drop_pct: float = 5.0
    gamma_min_premium_rise_pct: float = 8.0


DEFAULT_CONFIGS = (
    ScannerConfig("NIFTY", "WEEKLY", 0.8, -0.8),
    ScannerConfig("BANKNIFTY", "MONTHLY", 1.2, -1.5),
)


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def black_scholes_price(
    option_type: str, spot: float, strike: float, years: float,
    volatility: float, rate: float = 0.065, dividend_yield: float = 0.0,
) -> float:
    if min(spot, strike, years, volatility) <= 0:
        raise ValueError("spot, strike, years and volatility must be positive")
    root_t = sqrt(years)
    d1 = (log(spot / strike) + (rate - dividend_yield + 0.5 * volatility**2) * years) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    discounted_spot = spot * exp(-dividend_yield * years)
    discounted_strike = strike * exp(-rate * years)
    kind = option_type.strip().upper()
    if kind == "CE":
        return discounted_spot * _norm_cdf(d1) - discounted_strike * _norm_cdf(d2)
    if kind == "PE":
        return discounted_strike * _norm_cdf(-d2) - discounted_spot * _norm_cdf(-d1)
    raise ValueError("option_type must be CE or PE")


def implied_volatility(
    option_type: str, spot: float, strike: float, years: float, premium: float,
    rate: float = 0.065, dividend_yield: float = 0.0,
) -> float | None:
    """Return annualized IV as a decimal; reject prices outside BS bounds."""
    values = (spot, strike, years, premium, rate, dividend_yield)
    if not all(isfinite(float(v)) for v in values) or min(spot, strike, years, premium) <= 0:
        return None
    kind = option_type.strip().upper()
    if kind == "CE":
        lower = max(0.0, spot * exp(-dividend_yield * years) - strike * exp(-rate * years))
        upper = spot * exp(-dividend_yield * years)
    elif kind == "PE":
        lower = max(0.0, strike * exp(-rate * years) - spot * exp(-dividend_yield * years))
        upper = strike * exp(-rate * years)
    else:
        return None
    if premium < lower - 1e-8 or premium >= upper:
        return None
    lo, hi = 1e-5, 5.0
    try:
        if black_scholes_price(kind, spot, strike, years, hi, rate, dividend_yield) < premium:
            return None
        for _ in range(80):
            mid = (lo + hi) / 2.0
            model = black_scholes_price(kind, spot, strike, years, mid, rate, dividend_yield)
            if model < premium:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0
    except (ValueError, OverflowError, ZeroDivisionError):
        return None


def _expiry_years(expiry: str, now: datetime) -> float | None:
    """Expiry is valued to 15:30 IST; caller must supply an aware IST datetime."""
    from datetime import time
    from zoneinfo import ZoneInfo
    if now.tzinfo is None:
        return None
    try:
        date_value = None
        for date_format in ("%Y-%m-%d", "%d%b%Y", "%d%b%y", "%d-%b-%Y", "%d-%b-%y"):
            try:
                date_value = datetime.strptime(str(expiry).strip().upper(), date_format).date()
                break
            except ValueError:
                continue
        if date_value is None:
            return None
        close = datetime.combine(date_value, time(15, 30), tzinfo=ZoneInfo("Asia/Kolkata"))
        seconds = (close - now.astimezone(ZoneInfo("Asia/Kolkata"))).total_seconds()
        return seconds / (365.0 * 24 * 60 * 60) if seconds > 0 else None
    except (TypeError, ValueError):
        return None


def _fresh(quote: OptionQuote, now_ns: int, max_age_seconds: float) -> bool:
    # bool is an int subclass in Python; never treat True/False as provider time.
    return (
        isinstance(now_ns, int)
        and not isinstance(now_ns, bool)
        and isinstance(quote.timestamp_ns, int)
        and not isinstance(quote.timestamp_ns, bool)
        and quote.timestamp_ns > 0
        and now_ns >= quote.timestamp_ns
        and now_ns - quote.timestamp_ns <= max_age_seconds * 1_000_000_000
    )


def _iv(quote: OptionQuote, spot: float, years: float, config: ScannerConfig) -> float | None:
    mark = quote.mark
    if mark is None:
        return None
    return implied_volatility(
        quote.option_type, spot, quote.strike, years, mark,
        config.risk_free_rate, config.dividend_yield,
    )


def evaluate_skew(
    quotes: Iterable[OptionQuote], spot: float, now: datetime, now_ns: int,
    config: ScannerConfig,
) -> Signal:
    """Average CE-IV minus PE-IV at ATM and two matched OTM strike steps."""
    # The resolver must pass exactly one concrete expiry chain per evaluation.
    rows = [q for q in quotes if q.underlying.upper() == config.symbol and _fresh(q, now_ns, config.max_quote_age_seconds)]
    if len({q.expiry for q in rows}) > 1:
        return Signal("IV_SKEW", None, None, None, "WAITING", "multiple expiries supplied; isolate the configured expiry first")
    strikes = sorted({q.strike for q in rows})
    if not strikes or spot <= 0:
        return Signal("IV_SKEW", None, None, None, "WAITING", "fresh option chain or valid spot unavailable")
    atm_index = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    atm = strikes[atm_index]
    years = _expiry_years(rows[0].expiry, now)
    if years is None:
        return Signal("IV_SKEW", None, None, None, "WAITING", "expiry is expired or invalid")
    by_key = {(q.strike, q.option_type.upper()): q for q in rows}
    spreads = []
    for offset in (0, 1, 2):
        call_i, put_i = atm_index + offset, atm_index - offset
        if call_i >= len(strikes) or put_i < 0:
            continue
        call = by_key.get((strikes[call_i], "CE"))
        put = by_key.get((strikes[put_i], "PE"))
        if call is None or put is None:
            continue
        call_iv, put_iv = _iv(call, spot, years, config), _iv(put, spot, years, config)
        if call_iv is not None and put_iv is not None:
            spreads.append((call_iv - put_iv) * 100.0)
    if len(spreads) < 2:
        return Signal("IV_SKEW", None, None, None, "WAITING", "at least two matched CE/PE IV pairs are required")
    value = sum(spreads) / len(spreads)
    direction = "BULLISH" if value >= config.bullish_skew_threshold else "BEARISH" if value <= config.bearish_skew_threshold else None
    return Signal("IV_SKEW", direction, round(value, 4), config.bullish_skew_threshold if direction == "BULLISH" else config.bearish_skew_threshold if direction == "BEARISH" else None, "SIGNAL" if direction else "NEUTRAL", f"mean matched CE-IV minus PE-IV across {len(spreads)} strike pairs")


def evaluate_flip(
    quotes: Iterable[OptionQuote], spot: float, now: datetime, now_ns: int,
    config: ScannerConfig, previous_atm_diff: float | None,
) -> Signal:
    chain = [q for q in quotes if q.underlying.upper() == config.symbol]
    if len({q.expiry for q in chain}) > 1:
        return Signal("IV_FLIP", None, None, None, "WAITING", "multiple expiries supplied; isolate the configured expiry first")
    if not chain or spot <= 0:
        return Signal("IV_FLIP", None, None, None, "WAITING", "fresh ATM option quotes unavailable")
    # Select ATM from the complete resolved chain, then require both sides at
    # that exact strike to be fresh. Do not silently shift ATM when its ticks stale.
    atm = min({q.strike for q in chain}, key=lambda strike: abs(strike - spot))
    fresh_rows = [q for q in chain if _fresh(q, now_ns, config.max_quote_age_seconds)]
    pair = {q.option_type.upper(): q for q in fresh_rows if q.strike == atm}
    if "CE" not in pair or "PE" not in pair:
        return Signal("IV_FLIP", None, None, None, "WAITING", "fresh ATM option quotes unavailable: ATM CE and PE pair unavailable")
    years = _expiry_years(pair["CE"].expiry, now)
    if years is None:
        return Signal("IV_FLIP", None, None, None, "WAITING", "invalid or expired option expiry")
    call_iv = _iv(pair["CE"], spot, years, config)
    put_iv = _iv(pair["PE"], spot, years, config)
    if call_iv is None or put_iv is None:
        return Signal("IV_FLIP", None, None, None, "WAITING", "ATM IV could not be solved from valid option marks")
    diff = (call_iv - put_iv) * 100.0
    gap = config.min_flip_gap_iv_points
    crossed_up = previous_atm_diff is not None and previous_atm_diff <= -gap and diff >= gap
    crossed_down = previous_atm_diff is not None and previous_atm_diff >= gap and diff <= -gap
    direction = "BULLISH" if crossed_up else "BEARISH" if crossed_down else None
    return Signal("IV_FLIP", direction, round(diff, 4), gap, "SIGNAL" if direction else "NEUTRAL", "confirmed ATM IV crossover" if direction else "no confirmed crossover; hysteresis suppresses tick-noise flips")


def evaluate_gamma_blast(
    current: OptionQuote, prior: OptionQuote, *, min_oi_drop_pct: float = 5.0,
    min_premium_rise_pct: float = 8.0,
) -> Signal:
    """Detect writer-unwinding proxy: OI contracts while premium expands.

    Current and reference observations must describe the same option contract;
    otherwise a cross-strike/expiry comparison could manufacture a false signal.
    """
    same_contract = (
        current.underlying.strip().upper() == prior.underlying.strip().upper()
        and current.expiry.strip().upper() == prior.expiry.strip().upper()
        and current.strike == prior.strike
        and current.option_type.strip().upper() == prior.option_type.strip().upper()
        and current.token == prior.token
    )
    timestamps_ordered = (
        isinstance(current.timestamp_ns, int)
        and not isinstance(current.timestamp_ns, bool)
        and isinstance(prior.timestamp_ns, int)
        and not isinstance(prior.timestamp_ns, bool)
        and current.timestamp_ns > prior.timestamp_ns > 0
    )
    if not same_contract or not timestamps_ordered:
        return Signal(
            "GAMMA_BLAST", None, None, None, "WAITING",
            "current and reference samples must be ordered observations of the same option contract",
        )
    if current.oi is None or prior.oi is None or prior.oi <= 0:
        return Signal("GAMMA_BLAST", None, None, None, "WAITING", "valid current and five-minute reference OI required")
    current_mark, prior_mark = current.mark, prior.mark
    if current_mark is None or prior_mark is None or prior_mark <= 0:
        return Signal("GAMMA_BLAST", None, None, None, "WAITING", "valid current and five-minute reference premium required")
    oi_drop = (prior.oi - current.oi) / prior.oi * 100.0
    premium_rise = (current_mark - prior_mark) / prior_mark * 100.0
    if oi_drop >= min_oi_drop_pct and premium_rise >= min_premium_rise_pct:
        direction = "BULLISH" if current.option_type.upper() == "CE" else "BEARISH" if current.option_type.upper() == "PE" else None
        return Signal("GAMMA_BLAST", direction, round(oi_drop, 3), min_oi_drop_pct, "SIGNAL" if direction else "NEUTRAL", f"OI down {oi_drop:.2f}% while premium up {premium_rise:.2f}%")
    return Signal("GAMMA_BLAST", None, round(oi_drop, 3), min_oi_drop_pct, "NEUTRAL", f"OI change {oi_drop:.2f}%; premium change {premium_rise:.2f}%")


def confluence(signals: Iterable[Signal], required: int = 2) -> Signal:
    """Emit a directional alert only when >= required independent signals agree."""
    active = [s for s in signals if s.status == "SIGNAL" and s.direction in {"BULLISH", "BEARISH"}]
    counts = {direction: sum(s.direction == direction for s in active) for direction in ("BULLISH", "BEARISH")}
    winner = max(counts, key=counts.get)
    if counts[winner] >= required:
        return Signal("CONFLUENCE", winner, float(counts[winner]), float(required), "SIGNAL", f"{counts[winner]} independent strategies agree")
    return Signal("CONFLUENCE", None, float(max(counts.values())), float(required), "NEUTRAL", "insufficient same-direction confirmations")
