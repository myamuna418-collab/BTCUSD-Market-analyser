"""Five candidate masks transcribed from Untitled-1.ipynb, Combination 485.

IMPORTANT: Candidate generation is not the complete frozen signal engine.
Requires independently computed, time-aligned 1H ATR14, 3H momentum/ATR,
and 4H +DI/-DI from completed historical candles.
No orders are placed.
"""
from dataclasses import dataclass
from math import isfinite

PRIORITY = ("ORIGINAL_WICK", "CONTINUATION", "PULLBACK", "REVERSAL", "IMPULSE_PULLBACK")

@dataclass(frozen=True)
class Inputs:
    open: float
    high: float
    low: float
    close: float
    atr_1h_14: float
    plus_di_4h: float
    minus_di_4h: float
    momentum_3h_atr: float
    candle_closed: bool

def eligible_candidates(x: Inputs):
    """Return eligible (strategy, direction) pairs, without claiming a trade signal."""
    values = (x.open, x.high, x.low, x.close, x.atr_1h_14,
              x.plus_di_4h, x.minus_di_4h, x.momentum_3h_atr)
    if not x.candle_closed:
        raise ValueError("Unfinished candle: final strategy requires a completed signal candle")
    if not all(isfinite(v) for v in values):
        raise ValueError("Missing or nonfinite market indicator")
    if x.high < max(x.open, x.close) or x.low > min(x.open, x.close):
        raise ValueError("Invalid OHLC")
    if x.atr_1h_14 <= 0 or x.high <= x.low:
        raise ValueError("Invalid ATR or candle range")
    body = x.close - x.open
    body_size = abs(body)
    candle_range = x.high - x.low
    fraction = body_size / candle_range
    lower_wick = min(x.open, x.close) - x.low
    upper_wick = x.high - max(x.open, x.close)
    strong_buy = body > 0 and fraction >= 0.60
    strong_sell = body < 0 and fraction >= 0.60
    up = x.plus_di_4h > x.minus_di_4h
    down = x.minus_di_4h > x.plus_di_4h
    momentum_up = x.momentum_3h_atr > 0
    momentum_down = x.momentum_3h_atr < 0
    impulse = candle_range / x.atr_1h_14 >= 1.0 and fraction >= 0.60
    masks = {
        "ORIGINAL_WICK": (
            lower_wick > body_size and lower_wick > upper_wick and up,
            upper_wick > body_size and upper_wick > lower_wick and down),
        "CONTINUATION": (
            up and momentum_up and strong_buy,
            down and momentum_down and strong_sell),
        "PULLBACK": (
            up and momentum_up and strong_sell,
            down and momentum_down and strong_buy),
        "REVERSAL": (
            down and momentum_up and strong_buy,
            up and momentum_down and strong_sell),
        "IMPULSE_PULLBACK": (
            impulse and body > 0 and up,
            impulse and body < 0 and down),
    }
    return [(name, direction)
            for name in PRIORITY
            for direction, ok in zip(("BUY", "SELL"), masks[name])
            if ok]
