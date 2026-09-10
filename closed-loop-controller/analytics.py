#!/usr/bin/env python3
"""
analytics.py (Layer 2 -- NWDAF / Analytics)
Turns raw time-series history into a per-slice analytics summary:
current value, short-term trend (slope), moving average, and simple
anomaly flags. This is the "collect + aggregate + detect patterns" layer
sitting between raw observability and the forecaster.

Pure read-only over metrics_store -- no cluster/network calls here.
"""

from dataclasses import dataclass, field
from typing import Optional

from config import CFG
import metrics_store as store


@dataclass
class SliceAnalytics:
    slice_name: str
    ue_count: Optional[float] = None
    latency_ms: Optional[float] = None
    loss_pct: Optional[float] = None
    ue_count_trend: Optional[float] = None  # UEs per second, recent slope
    ue_count_history: list = field(default_factory=list)  # [(ts, value), ...]
    latency_anomaly: bool = False
    loss_anomaly: bool = False


def _slope(series):
    """Simple least-squares slope over (ts, value) pairs. None if <2 points."""
    if len(series) < 2:
        return None
    n = len(series)
    t0 = series[0][0]
    xs = [ts - t0 for ts, _ in series]
    ys = [v for _, v in series]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return num / den


def analyze_slice(slice_name, lookback_s=None):
    lookback_s = lookback_s or CFG.FORECAST_LOOKBACK_S

    ue_series = store.read_series(slice_name, "ue_count", lookback_s)
    lat_row = store.latest_value(slice_name, "latency_ms")
    loss_row = store.latest_value(slice_name, "loss_pct")

    a = SliceAnalytics(slice_name=slice_name)
    a.ue_count_history = ue_series
    a.ue_count = ue_series[-1][1] if ue_series else None
    a.ue_count_trend = _slope(ue_series)
    a.latency_ms = lat_row[1] if lat_row else None
    a.loss_pct = loss_row[1] if loss_row else None

    if a.latency_ms is not None and a.latency_ms >= CFG.LATENCY_ALERT_MS:
        a.latency_anomaly = True
    if a.loss_pct is not None and a.loss_pct >= CFG.LOSS_ALERT_PCT:
        a.loss_anomaly = True

    return a


def analyze_all():
    return {s: analyze_slice(s) for s in CFG.SLICES}


if __name__ == "__main__":
    for slice_name, res in analyze_all().items():
        print(f"{slice_name:6s}: ue={res.ue_count} trend={res.ue_count_trend} "
              f"lat={res.latency_ms} loss={res.loss_pct} "
              f"lat_anomaly={res.latency_anomaly} loss_anomaly={res.loss_anomaly} "
              f"(n={len(res.ue_count_history)} samples)")
