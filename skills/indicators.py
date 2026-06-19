"""
Technical indicators — 75+ implementations.

All indicators take a list[float] of close prices (and optionally
high/low/volume) and return a dict with the indicator value(s).

Reference: distilled from jesse-ai/jesse (176 indicators), freqtrade,
and standard TA literature. We picked the most useful ones for crypto.

Naming convention:
- All functions take `closes` as the first positional arg
- Optionally `highs`, `lows`, `volumes` (positional, in that order)
- Optional `period` kwarg (default varies)
- Return a dict like {"value": float} or {"macd": x, "signal": y, "hist": z}
- Return empty dict {} if input is too short

All values are computed in pure Python (no pandas / numpy needed).
"""

from typing import List, Optional, Dict, Any
import math


# =============================================================================
# Helpers
# =============================================================================

def _sma(values: List[float], period: int) -> List[float]:
    """Simple moving average. Returns list of same length, NaN-padded."""
    out = [float("nan")] * len(values)
    if period <= 0 or len(values) < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def _ema(values: List[float], period: int) -> List[float]:
    """Exponential moving average."""
    out = [float("nan")] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2 / (period + 1)
    # Seed with SMA of first `period`
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def _wilder(values: List[float], period: int) -> List[float]:
    """Wilder's smoothing (used in RSI, ATR, ADX)."""
    out = [float("nan")] * len(values)
    if period <= 0 or len(values) < period:
        return out
    # Seed with SMA
    out[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def _stdev(values: List[float], period: int) -> List[float]:
    """Rolling standard deviation."""
    out = [float("nan")] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        out[i] = math.sqrt(var)
    return out


def _true_ranges(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    """Compute true range series."""
    trs = [float("nan")] * len(closes)
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        trs[i] = max(hl, hc, lc)
    return trs


def _last_valid(values: List[float]) -> Optional[float]:
    """Last non-NaN value."""
    for v in reversed(values):
        if not math.isnan(v):
            return v
    return None


def _series(values: List[float]) -> List[float]:
    """Strip leading NaNs from a series."""
    return [v for v in values if not math.isnan(v)]


# =============================================================================
# TREND indicators (15)
# =============================================================================

def ichimoku(closes: List[float], highs: List[float], lows: List[float],
             tenkan: int = 9, kijun: int = 26, senkou_b: int = 52) -> Dict[str, Any]:
    """Ichimoku Cloud — Tenkan, Kijun, Senkou A/B, Chikou."""
    if len(closes) < senkou_b:
        return {}
    tenkan_line = [(max(highs[i - tenkan + 1:i + 1]) + min(lows[i - tenkan + 1:i + 1])) / 2
                   for i in range(tenkan - 1, len(closes))]
    kijun_line = [(max(highs[i - kijun + 1:i + 1]) + min(lows[i - kijun + 1:i + 1])) / 2
                  for i in range(kijun - 1, len(closes))]
    # Senkou A = (Tenkan + Kijun) / 2, plotted 26 periods ahead
    senkou_a = []
    for i in range(len(tenkan_line)):
        tenkan_idx = i + (tenkan - 1)
        kijun_idx = tenkan_idx - (kijun - tenkan)
        if 0 <= kijun_idx < len(kijun_line):
            senkou_a.append((tenkan_line[i] + kijun_line[kijun_idx]) / 2)
    senkou_b_line = [(max(highs[i - senkou_b + 1:i + 1]) + min(lows[i - senkou_b + 1:i + 1])) / 2
                     for i in range(senkou_b - 1, len(closes))]
    return {
        "tenkan": _last_valid(tenkan_line),
        "kijun": _last_valid(kijun_line),
        "senkou_a": _last_valid(senkou_a),
        "senkou_b": _last_valid(senkou_b_line),
        "chikou": closes[-1],
        "cloud_bullish": (
            _last_valid(senkou_a) and _last_valid(senkou_b_line)
            and _last_valid(senkou_a) > _last_valid(senkou_b_line)
        ),
    }


def supertrend(closes: List[float], highs: List[float], lows: List[float],
               period: int = 10, multiplier: float = 3.0) -> Dict[str, Any]:
    """ATR-based SuperTrend."""
    if len(closes) < period + 1:
        return {}
    trs = _true_ranges(highs, lows, closes)
    atr = _wilder(trs[1:], period)
    if not atr or _last_valid(atr) is None:
        return {}
    final_atr = _last_valid(atr)
    hl2 = (highs[-1] + lows[-1]) / 2
    upper = hl2 + multiplier * final_atr
    lower = hl2 - multiplier * final_atr
    return {
        "upper_band": round(upper, 6),
        "lower_band": round(lower, 6),
        "trend": "up" if closes[-1] > lower else "down",
        "support": round(lower, 6) if closes[-1] > lower else round(upper, 6),
        "resistance": round(upper, 6) if closes[-1] > lower else round(lower, 6),
    }


def parabolic_sar(highs: List[float], lows: List[float],
                  step: float = 0.02, max_step: float = 0.2) -> Dict[str, Any]:
    """Parabolic SAR (last value + trend direction)."""
    if len(highs) < 2:
        return {}
    is_long = True
    af = step
    ep = highs[0]
    sar = lows[0]
    for i in range(1, len(highs)):
        sar = sar + af * (ep - sar)
        if is_long:
            if lows[i] < sar:
                is_long = False
                sar = ep
                ep = lows[i]
                af = step
            else:
                if highs[i] > ep:
                    ep = highs[i]
                    af = min(af + step, max_step)
        else:
            if highs[i] > sar:
                is_long = True
                sar = ep
                ep = highs[i]
                af = step
            else:
                if lows[i] < ep:
                    ep = lows[i]
                    af = min(af + step, max_step)
    return {
        "sar": round(sar, 6),
        "trend": "up" if is_long else "down",
        "is_reversal": False,
    }


def aroon(highs: List[float], lows: List[float], period: int = 25) -> Dict[str, Any]:
    """Aroon Up / Down + Oscillator."""
    if len(highs) < period:
        return {}
    window_h = highs[-period:]
    window_l = lows[-period:]
    high_idx = window_h.index(max(window_h))
    low_idx = window_l.index(min(window_l))
    up = ((period - high_idx) / period) * 100
    down = ((period - low_idx) / period) * 100
    return {"aroon_up": round(up, 2), "aroon_down": round(down, 2), "oscillator": round(up - down, 2)}


def vortex(highs: List[float], lows: List[float], closes: List[float],
           period: int = 14) -> Dict[str, Any]:
    """Vortex Indicator (VI+ / VI-)."""
    if len(closes) < period + 1:
        return {}
    trs = _true_ranges(highs, lows, closes)
    plus_vm = [abs(highs[i] - lows[i - 1]) for i in range(1, len(highs))]
    minus_vm = [abs(lows[i] - highs[i - 1]) for i in range(1, len(highs))]
    sum_tr = sum(trs[i] for i in range(len(trs) - period, len(trs)) if not math.isnan(trs[i]))
    sum_plus = sum(plus_vm[i] for i in range(len(plus_vm) - period, len(plus_vm)))
    sum_minus = sum(minus_vm[i] for i in range(len(minus_vm) - period, len(minus_vm)))
    if sum_tr == 0:
        return {}
    return {
        "vi_plus": round(sum_plus / sum_tr, 4),
        "vi_minus": round(sum_minus / sum_tr, 4),
        "signal": "bullish_cross" if sum_plus > sum_minus else "bearish_cross",
    }


def ttm_squeeze(closes: List[float], highs: List[float], lows: List[float],
                bb_period: int = 20, kc_period: int = 20, kc_mult: float = 1.5) -> Dict[str, Any]:
    """TTM Squeeze: Bollinger inside Keltner = squeeze."""
    if len(closes) < bb_period:
        return {}
    sma_bb = sum(closes[-bb_period:]) / bb_period
    std = _stdev(closes[-bb_period:], bb_period)[-1]
    bb_upper = sma_bb + 2 * std
    bb_lower = sma_bb - 2 * std
    # Keltner
    trs = _true_ranges(highs, lows, closes)
    atr = _wilder(trs[1:], kc_period)
    if _last_valid(atr) is None:
        return {}
    kc_upper = sma_bb + kc_mult * _last_valid(atr)
    kc_lower = sma_bb - kc_mult * _last_valid(atr)
    is_squeeze = bb_lower > kc_lower and bb_upper < kc_upper
    # Momentum (close - close n periods ago)
    momentum = closes[-1] - closes[-bb_period] if len(closes) >= bb_period else 0
    return {
        "squeeze_on": is_squeeze,
        "momentum": round(momentum, 6),
        "signal": "squeeze_release_up" if not is_squeeze and momentum > 0 else
                  "squeeze_release_down" if not is_squeeze and momentum < 0 else
                  "squeeze_on",
    }


def qqe(closes: List[float], rsi_period: int = 14, sf: int = 5) -> Dict[str, Any]:
    """Quantitative Qualitative Estimation (QQE) — RSI + smoothed signal."""
    if len(closes) < rsi_period + sf + 1:
        return {}
    # Compute RSI
    rsi_values = rsi_series(closes, rsi_period)
    if not rsi_values or _last_valid(rsi_values) is None:
        return {}
    rsi_ma = _ema(rsi_values, sf)
    rsi_now = rsi_values[-1]
    sig_now = rsi_ma[-1] if not math.isnan(rsi_ma[-1]) else 50
    return {
        "rsi": round(rsi_now, 2),
        "signal": round(sig_now, 2),
        "above_signal": rsi_now > sig_now,
        "histogram": round(rsi_now - sig_now, 2),
    }


def halftrend(closes: List[float], highs: List[float], lows: List[float],
              period: int = 2, multiplier: float = 2.0) -> Dict[str, Any]:
    """HalfTrend — pivot-based trend."""
    if len(closes) < period + 1:
        return {}
    trs = _true_ranges(highs, lows, closes)
    atr = _wilder(trs[1:], period)
    if _last_valid(atr) is None:
        return {}
    hl_avg = (max(highs[-period:]) + min(lows[-period:])) / 2
    upper = hl_avg + multiplier * _last_valid(atr)
    lower = hl_avg - multiplier * _last_valid(atr)
    return {
        "upper_band": round(upper, 6),
        "lower_band": round(lower, 6),
        "trend": "up" if closes[-1] > lower else "down",
    }


def alligator(highs: List[float], lows: List[float],
              jaw_period: int = 13, teeth_period: int = 8, lips_period: int = 5,
              shift_jaw: int = 8, shift_teeth: int = 5, shift_lips: int = 3) -> Dict[str, Any]:
    """Williams Alligator — 3 smoothed MAs (Jaw, Teeth, Lips)."""
    if len(closes := []) < jaw_period + shift_jaw:  # type: ignore
        return {}
    hl_mid = [(highs[i] + lows[i]) / 2 for i in range(len(highs))]
    jaw = _sma(hl_mid, jaw_period)
    teeth = _sma(hl_mid, teeth_period)
    lips = _sma(hl_mid, lips_period)
    return {
        "jaw": _last_valid(jaw),
        "teeth": _last_valid(teeth),
        "lips": _last_valid(lips),
        "trend": "up" if (_last_valid(lips) or 0) > (_last_valid(teeth) or 0) > (_last_valid(jaw) or 0) else "down",
    }


def gator(highs: List[float], lows: List[float]) -> Dict[str, Any]:
    """Gator Oscillator — difference between Alligator lines."""
    a = alligator(highs, lows)
    if not a:
        return {}
    return {
        "jaw_teeth": round(abs((a["jaw"] or 0) - (a["teeth"] or 0)), 6),
        "teeth_lips": round(abs((a["teeth"] or 0) - (a["lips"] or 0)), 6),
    }


def dmi(highs: List[float], lows: List[float], closes: List[float],
        period: int = 14) -> Dict[str, Any]:
    """Directional Movement Index (+DI, -DI, ADX)."""
    if len(closes) < period + 1:
        return {}
    plus_dm = [max(highs[i] - highs[i - 1], 0) if highs[i] > highs[i - 1] and highs[i] - highs[i - 1] > lows[i - 1] - lows[i] else 0 for i in range(1, len(highs))]
    minus_dm = [max(lows[i - 1] - lows[i], 0) if lows[i - 1] > lows[i] and lows[i - 1] - lows[i] > highs[i] - highs[i - 1] else 0 for i in range(1, len(highs))]
    trs = _true_ranges(highs, lows, closes)
    atr_series = _wilder(trs[1:], period)
    if _last_valid(atr_series) is None or _last_valid(atr_series) == 0:
        return {}
    sum_plus_dm = _wilder(plus_dm, period)[-1]
    sum_minus_dm = _wilder(minus_dm, period)[-1]
    atr_now = _last_valid(atr_series)
    plus_di = (sum_plus_dm / atr_now) * 100
    minus_di = (sum_minus_dm / atr_now) * 100
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
    return {
        "plus_di": round(plus_di, 2),
        "minus_di": round(minus_di, 2),
        "adx": round(dx, 2),
        "trend": "up" if plus_di > minus_di else "down",
    }


def aroon_oscillator(highs: List[float], lows: List[float], period: int = 25) -> Dict[str, Any]:
    a = aroon(highs, lows, period)
    return {"oscillator": a.get("oscillator", 0)}


def dpo(closes: List[float], period: int = 20) -> Dict[str, Any]:
    """Detrended Price Oscillator."""
    if len(closes) < period + 1:
        return {}
    shift = period // 2 + 1
    sma = _sma(closes, period)
    if _last_valid(sma) is None:
        return {}
    ref_idx = len(closes) - shift
    if ref_idx < 0 or math.isnan(sma[ref_idx]):
        return {}
    return {"dpo": round(closes[-1] - sma[ref_idx], 6)}


def eom(closes: List[float], highs: List[float], lows: List[float],
        volumes: List[float], period: int = 14) -> Dict[str, Any]:
    """Ease of Movement."""
    if len(closes) < period + 1:
        return {}
    eom_values = []
    for i in range(1, len(closes)):
        dm = ((highs[i] + lows[i]) / 2) - ((highs[i - 1] + lows[i - 1]) / 2)
        br = (volumes[i] / 1_000_000) / (highs[i] - lows[i]) if (highs[i] - lows[i]) > 0 else 0
        eom_values.append(dm / br if br else 0)
    ema = _ema(eom_values, period)
    return {"eom": round(_last_valid(ema) or 0, 6)}


def tsi(closes: List[float], long_period: int = 25, short_period: int = 13) -> Dict[str, Any]:
    """True Strength Index."""
    if len(closes) < long_period + short_period:
        return {}
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    abs_diffs = [abs(d) for d in diffs]
    double_smooth_pc = _ema(_ema(diffs, long_period), short_period)
    double_smooth_abs = _ema(_ema(abs_diffs, long_period), short_period)
    pc = _last_valid(double_smooth_pc) or 0
    ab = _last_valid(double_smooth_abs) or 0
    return {"tsi": round(100 * (pc / ab) if ab else 0, 2)}


# =============================================================================
# MOMENTUM indicators (15)
# =============================================================================

def rsi_series(closes: List[float], period: int = 14) -> List[float]:
    """Full RSI series (helper for QQE etc.)."""
    if len(closes) < period + 1:
        return [float("nan")] * len(closes)
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    avg_g = _wilder(gains, period)
    avg_l = _wilder(losses, period)
    rsi = [float("nan")] * len(closes)
    for i in range(period, len(closes)):
        if math.isnan(avg_g[i - 1]) or math.isnan(avg_l[i - 1]) or (avg_g[i - 1] + avg_l[i - 1]) == 0:
            continue
        rs = avg_g[i - 1] / (avg_l[i - 1] if avg_l[i - 1] else 1e-10)
        rsi[i] = 100 - (100 / (1 + rs))
    return rsi


def rsi(closes: List[float], period: int = 14) -> Dict[str, Any]:
    s = rsi_series(closes, period)
    v = _last_valid(s)
    return {"rsi": round(v, 2) if v is not None else None,
            "overbought": v is not None and v > 70,
            "oversold": v is not None and v < 30}


def stochastic(highs: List[float], lows: List[float], closes: List[float],
               k_period: int = 14, d_period: int = 3) -> Dict[str, Any]:
    """Stochastic Oscillator (%K, %D)."""
    if len(closes) < k_period:
        return {}
    k_values = []
    for i in range(k_period - 1, len(closes)):
        hh = max(highs[i - k_period + 1:i + 1])
        ll = min(lows[i - k_period + 1:i + 1])
        k = ((closes[i] - ll) / (hh - ll) * 100) if (hh - ll) > 0 else 50
        k_values.append(k)
    d = _sma(k_values, d_period)
    k_now = k_values[-1]
    d_now = _last_valid(d)
    return {
        "k": round(k_now, 2),
        "d": round(d_now, 2) if d_now else None,
        "overbought": k_now > 80,
        "oversold": k_now < 20,
        "cross": "bullish" if d_now and k_now > d_now else "bearish" if d_now else None,
    }


def stoch_rsi(closes: List[float], rsi_period: int = 14, stoch_period: int = 14,
              k_period: int = 3, d_period: int = 3) -> Dict[str, Any]:
    """Stochastic RSI."""
    rsi_s = rsi_series(closes, rsi_period)
    valid = [v for v in rsi_s if not math.isnan(v)]
    if len(valid) < stoch_period:
        return {}
    stoch = []
    for i in range(stoch_period - 1, len(valid)):
        window = valid[i - stoch_period + 1:i + 1]
        lo, hi = min(window), max(window)
        stoch.append(((valid[i] - lo) / (hi - lo) * 100) if hi > lo else 50)
    k = _sma(stoch, k_period)
    d = _sma([v for v in k if not math.isnan(v)], d_period)
    return {"k": round(_last_valid(k) or 50, 2), "d": round(_last_valid(d) or 50, 2)}


def williams_r(highs: List[float], lows: List[float], closes: List[float],
               period: int = 14) -> Dict[str, Any]:
    """Williams %R."""
    if len(closes) < period:
        return {}
    hh = max(highs[-period:])
    ll = min(lows[-period:])
    r = ((hh - closes[-1]) / (hh - ll) * -100) if (hh - ll) > 0 else -50
    return {"williams_r": round(r, 2), "overbought": r > -20, "oversold": r < -80}


def cci(highs: List[float], lows: List[float], closes: List[float],
        period: int = 20) -> Dict[str, Any]:
    """Commodity Channel Index."""
    if len(closes) < period:
        return {}
    tps = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    sma_tp = _sma(tps, period)
    mean_dev = []
    for i in range(period - 1, len(tps)):
        m = sma_tp[i]
        if math.isnan(m):
            continue
        mean_dev.append(sum(abs(tps[j] - m) for j in range(i - period + 1, i + 1)) / period)
    if not mean_dev:
        return {}
    md = mean_dev[-1]
    if md == 0:
        return {"cci": 0}
    return {"cci": round((tps[-1] - sma_tp[-1]) / (0.015 * md), 2)}


def mfi(highs: List[float], lows: List[float], closes: List[float],
        volumes: List[float], period: int = 14) -> Dict[str, Any]:
    """Money Flow Index."""
    if len(closes) < period + 1:
        return {}
    tps = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    flows = []
    for i in range(1, len(tps)):
        mf = tps[i] * volumes[i]
        flows.append((mf, "pos" if tps[i] > tps[i - 1] else "neg" if tps[i] < tps[i - 1] else "flat"))
    pos = sum(f for f, t in flows[-period:] if t == "pos")
    neg = sum(f for f, t in flows[-period:] if t == "neg")
    if neg == 0:
        return {"mfi": 100, "overbought": True}
    ratio = pos / neg
    return {"mfi": round(100 - (100 / (1 + ratio)), 2), "overbought": ratio > 4, "oversold": ratio < 0.25}


def roc(closes: List[float], period: int = 12) -> Dict[str, Any]:
    """Rate of Change."""
    if len(closes) <= period:
        return {}
    return {"roc": round(((closes[-1] - closes[-period - 1]) / closes[-period - 1]) * 100, 2)}


def momentum_indicator(closes: List[float], period: int = 10) -> Dict[str, Any]:
    return {"momentum": round(closes[-1] - closes[-period - 1], 6)}


def ao(highs: List[float], lows: List[float], fast: int = 5, slow: int = 34) -> Dict[str, Any]:
    """Awesome Oscillator."""
    if len(closes := []) < slow:
        return {}
    mids = [(highs[i] + lows[i]) / 2 for i in range(len(highs))]
    sma_fast = _sma(mids, fast)
    sma_slow = _sma(mids, slow)
    f = _last_valid(sma_fast)
    s = _last_valid(sma_slow)
    if f is None or s is None:
        return {}
    return {"ao": round(f - s, 6), "bullish": f > s}


def apo(closes: List[float], fast: int = 12, slow: int = 26) -> Dict[str, Any]:
    """Absolute Price Oscillator."""
    if len(closes) < slow:
        return {}
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    f = _last_valid(ef)
    s = _last_valid(es)
    if f is None or s is None:
        return {}
    return {"apo": round(f - s, 6)}


def ppo(closes: List[float], fast: int = 12, slow: int = 26) -> Dict[str, Any]:
    """Percentage Price Oscillator."""
    if len(closes) < slow:
        return {}
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    f = _last_valid(ef)
    s = _last_valid(es)
    if f is None or s is None or s == 0:
        return {}
    return {"ppo": round(((f - s) / s) * 100, 2)}


def ult_osc(highs: List[float], lows: List[float], closes: List[float],
            p1: int = 7, p2: int = 14, p3: int = 28) -> Dict[str, Any]:
    """Ultimate Oscillator."""
    if len(closes) < p3 + 1:
        return {}
    trs = _true_ranges(highs, lows, closes)
    bp = [closes[i] - min(lows[i], closes[i - 1]) for i in range(1, len(closes))]
    avg1 = sum(bp[-p1:]) / sum(trs[-p1:]) if sum(trs[-p1:]) else 0
    avg2 = sum(bp[-p2:]) / sum(trs[-p2:]) if sum(trs[-p2:]) else 0
    avg3 = sum(bp[-p3:]) / sum(trs[-p3:]) if sum(trs[-p3:]) else 0
    return {"uo": round(100 * (4 * avg1 + 2 * avg2 + avg3) / 7, 2)}


def rsi_divergence(closes: List[float], lookback: int = 50) -> Dict[str, Any]:
    """Detect RSI divergence (bullish/bearish) over recent price swings."""
    if len(closes) < lookback + 14:
        return {}
    sub = closes[-lookback:]
    rsi_s = rsi_series(sub, 14)
    if not rsi_s or _last_valid(rsi_s) is None:
        return {}
    # Find recent swing lows and highs
    mid = lookback // 2
    first_low = min(sub[:mid])
    second_low = min(sub[mid:])
    first_rsi_low = min([v for v in rsi_s[:mid] if not math.isnan(v)] or [50])
    second_rsi_low = min([v for v in rsi_s[mid:] if not math.isnan(v)] or [50])
    first_high = max(sub[:mid])
    second_high = max(sub[mid:])
    first_rsi_high = max([v for v in rsi_s[:mid] if not math.isnan(v)] or [50])
    second_rsi_high = max([v for v in rsi_s[mid:] if not math.isnan(v)] or [50])
    bullish_div = second_low < first_low and second_rsi_low > first_rsi_low
    bearish_div = second_high > first_high and second_rsi_high < first_rsi_high
    return {
        "bullish_divergence": bullish_div,
        "bearish_divergence": bearish_div,
        "current_rsi": round(_last_valid(rsi_s), 2),
    }


def macd_signal_cross(closes: List[float], fast: int = 12, slow: int = 26, signal_p: int = 9) -> Dict[str, Any]:
    """Detect MACD signal line crosses (most recent cross)."""
    if len(closes) < slow + signal_p + 1:
        return {}
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    macd_line = [ef[i] - es[i] for i in range(len(closes))]
    signal_line = _ema(macd_line, signal_p)
    hist = [macd_line[i] - signal_line[i] if not math.isnan(signal_line[i]) else float("nan") for i in range(len(closes))]
    last_cross = None
    for i in range(1, len(macd_line)):
        if math.isnan(signal_line[i - 1]) or math.isnan(signal_line[i]):
            continue
        if macd_line[i - 1] <= signal_line[i - 1] and macd_line[i] > signal_line[i]:
            last_cross = ("bullish", i)
        elif macd_line[i - 1] >= signal_line[i - 1] and macd_line[i] < signal_line[i]:
            last_cross = ("bearish", i)
    return {
        "macd": round(macd_line[-1], 6) if not math.isnan(macd_line[-1]) else None,
        "signal": round(signal_line[-1], 6) if not math.isnan(signal_line[-1]) else None,
        "histogram": round(hist[-1], 6) if not math.isnan(hist[-1]) else None,
        "last_cross": last_cross[0] if last_cross else None,
        "cross_age_bars": (len(closes) - 1 - last_cross[1]) if last_cross else None,
    }


def coppock(closes: List[float], wma_period: int = 10, roc_long: int = 14, roc_short: int = 11) -> Dict[str, Any]:
    """Coppock Curve."""
    if len(closes) < wma_period + roc_long + roc_short:
        return {}
    roc1 = ((closes[-1] - closes[-roc_long - 1]) / closes[-roc_long - 1]) * 100
    roc2 = ((closes[-1] - closes[-roc_short - 1]) / closes[-roc_short - 1]) * 100
    # Use a simple WMA approximation (since true WMA is recursive)
    curve_value = roc1 + roc2
    return {"coppock": round(curve_value, 2)}


def fisher_transform(highs: List[float], lows: List[float], period: int = 10) -> Dict[str, Any]:
    """Fisher Transform — sharpens turning points."""
    if len(highs) < period:
        return {}
    hl_mid = [(highs[i] + lows[i]) / 2 for i in range(len(highs))]
    # Normalize to [-1, 1] over the period
    recent = hl_mid[-period:]
    lo = min(recent)
    hi = max(recent)
    if hi == lo:
        return {"fisher": 0, "trigger": 0}
    val = 0.33 * 2 * ((recent[-1] - lo) / (hi - lo) - 0.5) + 0.67 * 0  # simplified
    fisher = math.log((1 + val) / (1 - val)) if abs(val) < 1 else 0
    return {"fisher": round(fisher, 4)}


# =============================================================================
# VOLATILITY indicators (12)
# =============================================================================

def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Dict[str, Any]:
    """Welles Wilder ATR."""
    if len(closes) < period + 1:
        return {}
    trs = _true_ranges(highs, lows, closes)
    a = _wilder(trs[1:], period)
    v = _last_valid(a)
    return {"atr": round(v, 6) if v else None}


def natr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Dict[str, Any]:
    """Normalized ATR (% of price)."""
    a = atr(highs, lows, closes, period)
    if not a or not a["atr"] or closes[-1] == 0:
        return {}
    return {"natr": round((a["atr"] / closes[-1]) * 100, 2)}


def atr_pct(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Dict[str, Any]:
    return natr(highs, lows, closes, period)


def bollinger_width(closes: List[float], period: int = 20) -> Dict[str, Any]:
    if len(closes) < period:
        return {}
    sma = sum(closes[-period:]) / period
    std = _stdev(closes[-period:], period)[-1]
    upper = sma + 2 * std
    lower = sma - 2 * std
    return {"width": round(upper - lower, 6), "width_pct": round((upper - lower) / sma * 100, 2) if sma else 0}


def bollinger_pct_b(closes: List[float], period: int = 20) -> Dict[str, Any]:
    if len(closes) < period:
        return {}
    sma = sum(closes[-period:]) / period
    std = _stdev(closes[-period:], period)[-1]
    upper = sma + 2 * std
    lower = sma - 2 * std
    if upper == lower:
        return {"pct_b": 0.5}
    return {"pct_b": round((closes[-1] - lower) / (upper - lower), 4)}


def keltner(closes: List[float], highs: List[float], lows: List[float],
            ema_period: int = 20, atr_period: int = 10, multiplier: float = 2.0) -> Dict[str, Any]:
    if len(closes) < max(ema_period, atr_period):
        return {}
    e = _ema(closes, ema_period)
    a = atr(highs, lows, closes, atr_period)
    if not a or not a["atr"] or _last_valid(e) is None:
        return {}
    mid = _last_valid(e)
    return {
        "upper": round(mid + multiplier * a["atr"], 6),
        "middle": round(mid, 6),
        "lower": round(mid - multiplier * a["atr"], 6),
    }


def donchian(highs: List[float], lows: List[float], period: int = 20) -> Dict[str, Any]:
    if len(highs) < period:
        return {}
    return {
        "upper": round(max(highs[-period:]), 6),
        "lower": round(min(lows[-period:]), 6),
        "middle": round((max(highs[-period:]) + min(lows[-period:])) / 2, 6),
    }


def chandelier(highs: List[float], lows: List[float], closes: List[float],
               period: int = 22, multiplier: float = 3.0) -> Dict[str, Any]:
    """Chandelier Exit (long)."""
    if len(closes) < period + 1:
        return {}
    a = atr(highs, lows, closes, period)
    if not a or not a["atr"]:
        return {}
    return {
        "long_exit": round(max(highs[-period:]) - multiplier * a["atr"], 6),
        "short_exit": round(min(lows[-period:]) + multiplier * a["atr"], 6),
    }


def historical_volatility(closes: List[float], period: int = 30, annualize: bool = True) -> Dict[str, Any]:
    if len(closes) < period + 1:
        return {}
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    window = rets[-period:]
    mean = sum(window) / period
    var = sum((r - mean) ** 2 for r in window) / (period - 1)
    daily = math.sqrt(var)
    return {"hv_daily": round(daily, 6), "hv_annualized": round(daily * math.sqrt(365) * 100, 2) if annualize else None}


def ulcer_index(closes: List[float], period: int = 14) -> Dict[str, Any]:
    """Ulcer Index — downside volatility measure."""
    if len(closes) < period:
        return {}
    sub = closes[-period:]
    peak = sub[0]
    sq_sum = 0
    for c in sub:
        if c > peak:
            peak = c
        pct_drawdown = ((c - peak) / peak * 100) if peak > 0 else 0
        sq_sum += pct_drawdown ** 2
    return {"ulcer_index": round(math.sqrt(sq_sum / period), 4)}


def stddev(closes: List[float], period: int = 20) -> Dict[str, Any]:
    if len(closes) < period:
        return {}
    return {"stddev": round(_stdev(closes, period)[-1], 6)}


def chaikin_volatility(highs: List[float], lows: List[float], period: int = 10, roc_period: int = 10) -> Dict[str, Any]:
    if len(highs) < period + roc_period:
        return {}
    spread = [(highs[i] - lows[i]) for i in range(len(highs))]
    ema_spread = _ema(spread, period)
    now = _last_valid(ema_spread)
    prev_idx = max(0, len(ema_spread) - 1 - roc_period)
    prev = ema_spread[prev_idx] if not math.isnan(ema_spread[prev_idx]) else now
    if prev == 0 or prev is None:
        return {}
    return {"chaikin_vol": round(((now - prev) / prev) * 100, 2)}


# =============================================================================
# VOLUME indicators (12)
# =============================================================================

def obv(closes: List[float], volumes: List[float]) -> Dict[str, Any]:
    """On Balance Volume."""
    if len(closes) < 2:
        return {}
    obv_val = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv_val += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv_val -= volumes[i]
    return {"obv": round(obv_val, 2), "trend": "accumulation" if obv_val > 0 else "distribution"}


def ad_line(highs: List[float], lows: List[float], closes: List[float], volumes: List[float]) -> Dict[str, Any]:
    """Accumulation/Distribution Line."""
    if len(closes) < 1:
        return {}
    ad = 0
    for i in range(len(closes)):
        if highs[i] != lows[i]:
            mfm = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / (highs[i] - lows[i])
            ad += mfm * volumes[i]
    return {"ad_line": round(ad, 2)}


def adosc(highs: List[float], lows: List[float], closes: List[float], volumes: List[float],
          fast: int = 3, slow: int = 10) -> Dict[str, Any]:
    """Chaikin A/D Oscillator."""
    if len(closes) < slow:
        return {}
    adl = []
    for i in range(len(closes)):
        if highs[i] != lows[i]:
            mfm = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / (highs[i] - lows[i])
            adl.append(mfm * volumes[i])
        else:
            adl.append(0)
    fast_ema = _ema(adl, fast)
    slow_ema = _ema(adl, slow)
    return {"adosc": round((_last_valid(fast_ema) or 0) - (_last_valid(slow_ema) or 0), 2)}


def cmf(highs: List[float], lows: List[float], closes: List[float], volumes: List[float],
        period: int = 20) -> Dict[str, Any]:
    """Chaikin Money Flow."""
    if len(closes) < period:
        return {}
    mfv_sum = 0
    vol_sum = 0
    for i in range(len(closes) - period, len(closes)):
        if highs[i] != lows[i]:
            mfm = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / (highs[i] - lows[i])
            mfv_sum += mfm * volumes[i]
        vol_sum += volumes[i]
    return {"cmf": round(mfv_sum / vol_sum, 4) if vol_sum else 0,
            "buying_pressure": mfv_sum / vol_sum > 0 if vol_sum else False}


def vwap(highs: List[float], lows: List[float], closes: List[float], volumes: List[float]) -> Dict[str, Any]:
    """Session VWAP (cumulative from start of data)."""
    if not volumes or sum(volumes) == 0:
        return {}
    cum_pv = 0
    cum_v = 0
    for i in range(len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        cum_pv += tp * volumes[i]
        cum_v += volumes[i]
    return {"vwap": round(cum_pv / cum_v, 6) if cum_v else 0,
            "above_vwap": closes[-1] > (cum_pv / cum_v) if cum_v else False}


def vwma(closes: List[float], volumes: List[float], period: int = 20) -> Dict[str, Any]:
    if len(closes) < period:
        return {}
    pv = sum(closes[-period:][i] * volumes[-period:][i] for i in range(period))
    vs = sum(volumes[-period:])
    return {"vwma": round(pv / vs, 6) if vs else 0}


def emv(highs: List[float], lows: List[float], volumes: List[float], period: int = 14) -> Dict[str, Any]:
    """Ease of Movement Value."""
    if len(highs) < period + 1:
        return {}
    emv_vals = []
    for i in range(1, len(highs)):
        dm = (highs[i] + lows[i]) / 2 - (highs[i - 1] + lows[i - 1]) / 2
        br = (volumes[i] / 1e6) / (highs[i] - lows[i]) if (highs[i] - lows[i]) > 0 else 0
        emv_vals.append(dm / br if br else 0)
    ema = _ema(emv_vals, period)
    return {"emv": round(_last_valid(ema) or 0, 6)}


def fi(closes: List[float], volumes: List[float], period: int = 13) -> Dict[str, Any]:
    """Force Index."""
    if len(closes) < period + 1:
        return {}
    fi_vals = [(closes[i] - closes[i - 1]) * volumes[i] for i in range(1, len(closes))]
    ema = _ema(fi_vals, period)
    return {"force_index": round(_last_valid(ema) or 0, 2)}


def nvi(closes: List[float], volumes: List[float]) -> Dict[str, Any]:
    """Negative Volume Index."""
    if len(closes) < 2:
        return {}
    nvi_val = 1000
    for i in range(1, len(closes)):
        if volumes[i] < volumes[i - 1]:
            nvi_val = nvi_val + (closes[i] - closes[i - 1]) / closes[i - 1] * nvi_val
    return {"nvi": round(nvi_val, 2)}


def pvi(closes: List[float], volumes: List[float]) -> Dict[str, Any]:
    """Positive Volume Index."""
    if len(closes) < 2:
        return {}
    pvi_val = 1000
    for i in range(1, len(closes)):
        if volumes[i] > volumes[i - 1]:
            pvi_val = pvi_val + (closes[i] - closes[i - 1]) / closes[i - 1] * pvi_val
    return {"pvi": round(pvi_val, 2)}


def pvt(closes: List[float], volumes: List[float]) -> Dict[str, Any]:
    """Price Volume Trend."""
    if len(closes) < 2:
        return {}
    pvt_val = 0
    for i in range(1, len(closes)):
        pvt_val += ((closes[i] - closes[i - 1]) / closes[i - 1]) * volumes[i]
    return {"pvt": round(pvt_val, 2)}


def volume_profile(closes: List[float], volumes: List[float], bins: int = 10) -> Dict[str, Any]:
    """Volume-by-price histogram (POC = price with most volume)."""
    if len(closes) < 2:
        return {}
    lo, hi = min(closes), max(closes)
    if lo == hi:
        return {"poc": lo, "total_volume": sum(volumes)}
    width = (hi - lo) / bins
    hist = [0.0] * bins
    for i, c in enumerate(closes):
        idx = min(int((c - lo) / width), bins - 1)
        hist[idx] += volumes[i]
    poc_idx = hist.index(max(hist))
    poc_price = lo + (poc_idx + 0.5) * width
    return {
        "poc": round(poc_price, 6),
        "value_area_low": round(lo + poc_idx * width, 6),
        "value_area_high": round(lo + (poc_idx + 1) * width, 6),
        "total_volume": sum(volumes),
        "histogram_bins": [round(v, 2) for v in hist],
    }


# =============================================================================
# MOVING AVERAGES (12)
# =============================================================================

def kama(closes: List[float], period: int = 10, fast: int = 2, slow: int = 30) -> Dict[str, Any]:
    """Kaufman Adaptive MA."""
    if len(closes) < period + 1:
        return {}
    direction = abs(closes[-1] - closes[-period - 1])
    volatility = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - period, len(closes)))
    er = direction / volatility if volatility else 0
    sc = (er * (2 / (fast + 1) - 2 / (slow + 1)) + 2 / (slow + 1)) ** 2
    # KAMA = prev_kama + sc * (close - prev_kama)
    kama_val = closes[-period - 1]
    for i in range(-period, 0):
        kama_val = kama_val + sc * (closes[i] - kama_val)
    return {"kama": round(kama_val, 6)}


def frama(closes: List[float], period: int = 16) -> Dict[str, Any]:
    """Fractal Adaptive MA (simplified)."""
    if len(closes) < period:
        return {}
    h1 = max(closes[-(period // 2):])
    l1 = min(closes[-(period // 2):])
    h2 = max(closes[-period:-(period // 2)])
    l2 = min(closes[-period:-(period // 2)])
    h3 = max(closes[-period:])
    l3 = min(closes[-period:])
    n1 = (h1 - l1) / (period / 2)
    n2 = (h2 - l2) / (period / 2)
    n3 = (h3 - l3) / period
    dim = (math.log(n1 + n2) - math.log(n3)) / math.log(2) if (n1 + n2) > 0 and n3 > 0 else 1
    alpha = max(0.01, min(1, math.exp(-4.6 * (dim - 1))))
    return {"frama": round(alpha * closes[-1] + (1 - alpha) * closes[-2], 6)}


def alma(closes: List[float], period: int = 9, offset: float = 0.85, sigma: float = 6.0) -> Dict[str, Any]:
    if len(closes) < period:
        return {}
    m = offset * (period - 1)
    s = period / sigma
    w_sum = 0
    alma_val = 0
    for i in range(period):
        w = math.exp(-((i - m) ** 2) / (2 * s * s))
        alma_val += closes[-period + i] * w
        w_sum += w
    return {"alma": round(alma_val / w_sum, 6)}


def hma(closes: List[float], period: int = 9) -> Dict[str, Any]:
    """Hull MA: WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""
    if len(closes) < int(math.sqrt(period)) + period // 2:
        return {}
    half = period // 2
    sqrt_p = int(math.sqrt(period))
    # Simple WMA approximation (linear weights)
    def wma(values, p):
        weights = list(range(1, p + 1))
        return sum(values[-p + i] * weights[i] for i in range(p)) / sum(weights)
    inner = 2 * wma(closes, half) - wma(closes, period)
    # Build a temp series for the final WMA
    temp = list(closes[:-sqrt_p]) + [inner]
    return {"hma": round(wma(temp, sqrt_p), 6)}


def mcginley(closes: List[float], period: int = 14) -> Dict[str, Any]:
    if len(closes) < period + 1:
        return {}
    mg = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        denom = max(abs(closes[i] - mg), 0.0001)
        mg = mg + (closes[i] - mg) / (period * (closes[i] / mg) ** 4 if mg > 0 else 1) if denom else mg
    return {"mcginley": round(mg, 6)}


def t3(closes: List[float], period: int = 5, a: float = 0.7) -> Dict[str, Any]:
    if len(closes) < period * 6:
        return {}
    e1 = _ema(closes, period)
    e2 = _ema(e1, period)
    e3 = _ema(e2, period)
    e4 = _ema(e3, period)
    e5 = _ema(e4, period)
    e6 = _ema(e5, period)
    c1 = -a ** 3
    c2 = 3 * a ** 2 + 3 * a ** 3
    c3 = -3 * a - 6 * a ** 2 - 3 * a ** 3
    c4 = 1 + 3 * a + 3 * a ** 2 + a ** 3
    v6, v5, v4, v3 = _last_valid(e6), _last_valid(e5), _last_valid(e4), _last_valid(e3)
    if v6 is None or v5 is None or v4 is None or v3 is None:
        return {}
    t3_val = c1 * v6 + c2 * v5 + c3 * v4 + c4 * v3
    return {"t3": round(t3_val, 6)}


def vwap_ma(closes: List[float], volumes: List[float], period: int = 20) -> Dict[str, Any]:
    return vwma(closes, volumes, period)


def zlema(closes: List[float], period: int = 14) -> Dict[str, Any]:
    """Zero-Lag EMA."""
    if len(closes) < period:
        return {}
    lag = (period - 1) // 2
    src = [closes[i] + (closes[i] - closes[i - lag] if i >= lag else 0) for i in range(len(closes))]
    return {"zlema": round(_last_valid(_ema(src, period)) or closes[-1], 6)}


def tema(closes: List[float], period: int = 14) -> Dict[str, Any]:
    """Triple EMA."""
    if len(closes) < period * 3:
        return {}
    e1 = _ema(closes, period)
    e2 = _ema(e1, period)
    e3 = _ema(e2, period)
    v1, v2, v3 = _last_valid(e1), _last_valid(e2), _last_valid(e3)
    if v1 is None or v2 is None or v3 is None:
        return {}
    return {"tema": round(3 * v1 - 3 * v2 + v3, 6)}


def smma(closes: List[float], period: int = 14) -> Dict[str, Any]:
    return {"smma": round(_last_valid(_wilder(closes, period)) or closes[-1], 6)}


def wilders(closes: List[float], period: int = 14) -> Dict[str, Any]:
    return smma(closes, period)


def garman_klass(highs: List[float], lows: List[float], opens: Optional[List[float]] = None,
                 closes: Optional[List[float]] = None, period: int = 20) -> Dict[str, Any]:
    """Garman-Klass volatility estimator (annualized)."""
    if len(highs) < period:
        return {}
    if opens is None:
        opens = closes or lows
    if closes is None:
        closes = [(highs[i] + lows[i]) / 2 for i in range(len(highs))]
    sum_log_hl = sum(math.log(highs[i] / lows[i]) ** 2 for i in range(-period, 0) if lows[i] > 0)
    sum_log_co = sum(math.log(closes[i] / opens[i]) ** 2 for i in range(-period, 0) if opens[i] > 0)
    gk = math.sqrt((sum_log_hl - (2 * math.log(2) - 1) * sum_log_co) / period) * math.sqrt(365)
    return {"garman_klass": round(gk * 100, 2)}


# =============================================================================
# STATISTICAL / REGIME (9)
# =============================================================================

def beta(closes: List[float], benchmark: List[float], period: int = 60) -> Dict[str, Any]:
    """Beta vs benchmark (typically BTC)."""
    if len(closes) < period + 1 or len(benchmark) < period + 1:
        return {}
    rets_a = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(len(closes) - period, len(closes))]
    rets_b = [(benchmark[i] - benchmark[i - 1]) / benchmark[i - 1] for i in range(len(benchmark) - period, len(benchmark))]
    mean_a = sum(rets_a) / period
    mean_b = sum(rets_b) / period
    cov = sum((rets_a[i] - mean_a) * (rets_b[i] - mean_b) for i in range(period)) / (period - 1)
    var_b = sum((r - mean_b) ** 2 for r in rets_b) / (period - 1)
    return {"beta": round(cov / var_b, 4) if var_b else 0}


def correlation(asset_a: List[float], asset_b: List[float], period: int = 30) -> Dict[str, Any]:
    if len(asset_a) < period + 1 or len(asset_b) < period + 1:
        return {}
    a = asset_a[-period:]
    b = asset_b[-period:]
    mean_a = sum(a) / period
    mean_b = sum(b) / period
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(period)) / period
    std_a = math.sqrt(sum((x - mean_a) ** 2 for x in a) / period)
    std_b = math.sqrt(sum((x - mean_b) ** 2 for x in b) / period)
    if std_a == 0 or std_b == 0:
        return {"correlation": 0}
    return {"correlation": round(cov / (std_a * std_b), 4)}


def hurst(closes: List[float], max_lag: int = 20) -> Dict[str, Any]:
    """Hurst exponent — H>0.5 trending, H<0.5 mean-reverting."""
    if len(closes) < max_lag * 2:
        return {}
    lags = range(2, max_lag)
    tau = []
    for lag in lags:
        diffs = [closes[i] - closes[i - lag] for i in range(lag, len(closes))]
        std = math.sqrt(sum((d - sum(diffs) / len(diffs)) ** 2 for d in diffs) / len(diffs))
        tau.append(std)
    if not tau or all(t == 0 for t in tau):
        return {"hurst": 0.5}
    log_lags = [math.log(l) for l in lags]
    log_tau = [math.log(t) if t > 0 else 0 for t in tau]
    n = len(log_lags)
    mean_x = sum(log_lags) / n
    mean_y = sum(log_tau) / n
    num = sum((log_lags[i] - mean_x) * (log_tau[i] - mean_y) for i in range(n))
    den = sum((log_lags[i] - mean_x) ** 2 for i in range(n))
    hurst_val = num / den if den else 0.5
    return {"hurst": round(hurst_val, 3),
            "regime": "trending" if hurst_val > 0.55 else "mean_reverting" if hurst_val < 0.45 else "random_walk"}


def linear_regression(closes: List[float], period: int = 20) -> Dict[str, Any]:
    if len(closes) < period:
        return {}
    sub = closes[-period:]
    xs = list(range(period))
    mean_x = sum(xs) / period
    mean_y = sum(sub) / period
    slope = sum((xs[i] - mean_x) * (sub[i] - mean_y) for i in range(period)) / sum((x - mean_x) ** 2 for x in xs)
    intercept = mean_y - slope * mean_x
    predicted = intercept + slope * (period - 1)
    return {
        "slope": round(slope, 6),
        "intercept": round(intercept, 6),
        "predicted": round(predicted, 6),
        "r_squared": round(1 - sum((sub[i] - (intercept + slope * xs[i])) ** 2 for i in range(period)) /
                          sum((y - mean_y) ** 2 for y in sub), 4),
    }


def zscore(closes: List[float], period: int = 20) -> Dict[str, Any]:
    if len(closes) < period:
        return {}
    sub = closes[-period:]
    mean = sum(sub) / period
    std = math.sqrt(sum((x - mean) ** 2 for x in sub) / period)
    return {"zscore": round((closes[-1] - mean) / std, 2) if std else 0}


def skew(closes: List[float], period: int = 30) -> Dict[str, Any]:
    if len(closes) < period + 1:
        return {}
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(len(closes) - period, len(closes))]
    mean = sum(rets) / period
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / period)
    if std == 0:
        return {"skewness": 0}
    return {"skewness": round(sum((r - mean) ** 3 for r in rets) / (period * std ** 3), 4)}


def kurtosis(closes: List[float], period: int = 30) -> Dict[str, Any]:
    if len(closes) < period + 1:
        return {}
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(len(closes) - period, len(closes))]
    mean = sum(rets) / period
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / period)
    if std == 0:
        return {"kurtosis": 3}
    return {"kurtosis": round(sum((r - mean) ** 4 for r in rets) / (period * std ** 4), 4)}


def variance(closes: List[float], period: int = 20) -> Dict[str, Any]:
    if len(closes) < period:
        return {}
    sub = closes[-period:]
    mean = sum(sub) / period
    return {"variance": round(sum((x - mean) ** 2 for x in sub) / period, 6)}


def quantile(closes: List[float], period: int = 30) -> Dict[str, Any]:
    if len(closes) < period:
        return {}
    sub = sorted(closes[-period:])
    return {
        "q10": round(sub[int(period * 0.1)], 6),
        "q25": round(sub[int(period * 0.25)], 6),
        "q50": round(sub[int(period * 0.5)], 6),
        "q75": round(sub[int(period * 0.75)], 6),
        "q90": round(sub[int(period * 0.9)], 6),
    }
