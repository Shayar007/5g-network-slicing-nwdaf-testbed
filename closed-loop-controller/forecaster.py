#!/usr/bin/env python3
"""
forecaster.py (Layer 3 -- ML Forecasting)

Manual Holt's Linear Trend (double exponential smoothing) implementation.
No statsmodels dependency (matches the repo's existing constraint: no
internet access on this host, statsmodels unavailable).

IMPORTANT -- this is the actual fix for the "Holt gives bad accuracy"
problem from the three_way_comparison.py results: that run used n=5
samples. Forecast quality is a function of HISTORY DEPTH (more polls
over more time), not UE count. This module refuses to forecast below
CFG.MIN_FORECAST_SAMPLES and tells the caller so explicitly, instead of
silently returning a low-confidence number -- callers (policy_engine)
must fall back to reactive thresholds in that case.
"""

from dataclasses import dataclass
from typing import List, Optional

from config import CFG
import metrics_store as store


@dataclass
class ForecastResult:
    slice_name: str
    ok: bool
    reason: str = ""
    forecast_values: Optional[List[float]] = None  # next N steps
    level: Optional[float] = None
    trend: Optional[float] = None


def holt_linear(values: List[float], alpha: float, beta: float, steps_ahead: int):
    """
    Classic Holt's linear trend method.
    level[0]  = values[0]
    trend[0]  = values[1] - values[0]
    level[t]  = alpha * values[t] + (1 - alpha) * (level[t-1] + trend[t-1])
    trend[t]  = beta  * (level[t] - level[t-1]) + (1 - beta) * trend[t-1]
    forecast(h) = level[T] + h * trend[T]
    """
    if len(values) < 2:
        raise ValueError("need at least 2 values")

    level = values[0]
    trend = values[1] - values[0]

    for v in values[1:]:
        prev_level = level
        level = alpha * v + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend

    forecasts = [level + h * trend for h in range(1, steps_ahead + 1)]
    return forecasts, level, trend


def forecast_slice(slice_name, metric="ue_count", steps_ahead=None):
    steps_ahead = steps_ahead or CFG.FORECAST_STEPS_AHEAD
    series = store.read_series(slice_name, metric, CFG.FORECAST_LOOKBACK_S)

    if len(series) < CFG.MIN_FORECAST_SAMPLES:
        return ForecastResult(
            slice_name=slice_name,
            ok=False,
            reason=(f"only {len(series)} samples in last "
                    f"{CFG.FORECAST_LOOKBACK_S}s, need {CFG.MIN_FORECAST_SAMPLES} "
                    f"-- let history_writer run longer before trusting this"),
        )

    values = [v for _, v in series]
    try:
        forecasts, level, trend = holt_linear(values, CFG.HOLT_ALPHA, CFG.HOLT_BETA, steps_ahead)
    except ValueError as e:
        return ForecastResult(slice_name=slice_name, ok=False, reason=str(e))

    return ForecastResult(
        slice_name=slice_name,
        ok=True,
        forecast_values=forecasts,
        level=level,
        trend=trend,
    )


def forecast_all(metric="ue_count"):
    return {s: forecast_slice(s, metric=metric) for s in CFG.SLICES}


if __name__ == "__main__":
    for slice_name, res in forecast_all().items():
        if res.ok:
            print(f"{slice_name:6s}: forecast(next {len(res.forecast_values)}) = "
                  f"{[round(v, 2) for v in res.forecast_values]} "
                  f"(level={res.level:.2f}, trend={res.trend:+.3f}/poll)")
        else:
            print(f"{slice_name:6s}: NO FORECAST -- {res.reason}")
