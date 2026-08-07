#!/usr/bin/env python3
"""
ml_predictor.py
Standalone, read-only ML traffic-prediction service. Does NOT control
anything -- it only reads historical UE-count metrics (from Prometheus
live, OR a historical CSV export -- see data_source.py), trains a
lightweight regression model per slice, and pushes predicted near-future
UE counts as new Prometheus metrics for visualization.

This is intentionally decoupled from nwdaf-slice-controller (the
production threshold-based auto-scaler). If this script crashes or
produces bad predictions, the production auto-scaling is completely
unaffected -- it never reads anything this script writes.

Approach: per slice, fit a simple linear regression on the last N
minutes of slice_ue_count history (time -> count), and extrapolate
forward PREDICT_HORIZON_SECONDS. This is a legitimate, explainable
time-series forecasting technique (not a black box), appropriate given
the small amount of historical data available in a short-lived testbed.
An exponential-weighted anomaly flag is also computed: if the current
value deviates more than ANOMALY_Z_THRESH standard deviations from the
recent rolling mean, it's flagged as anomalous.

DATA SOURCE (2026-08-06 update, P1 production-readiness item)
----------------------------------------------------------------
History is now fetched via data_source.get_history(), which is a
pluggable abstraction over either:
  DATA_SOURCE=prometheus (default) -- current live-testbed behavior,
    unchanged from the original version of this script.
  DATA_SOURCE=csv -- reads a historical export CSV instead (see
    data_source.py docstring for expected columns). This is the
    realistic path for real operator data (Airtel/Jio/Vi or similar),
    which will most likely arrive as an offline export, not a live feed.
No other logic in this file changed as part of that update -- the
model-fitting, anomaly-check, and metric-pushing code is identical to
the original Prometheus-only version.

Usage:
  python3 ml_predictor.py
  DATA_SOURCE=csv HISTORICAL_DATA_CSV=/path/to/data.csv python3 ml_predictor.py
Requires: numpy, scikit-learn, requests (only needed for prometheus mode),
  prometheus_client
  pip install numpy scikit-learn requests prometheus_client --break-system-packages
"""

import os
import time
import logging
from datetime import datetime

import numpy as np
from sklearn.linear_model import LinearRegression
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from data_source import get_history

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [ml-predictor] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

PUSHGATEWAY_ADDR = os.getenv("PUSHGATEWAY_ADDR", "localhost:9091")
JOB_NAME = "oai_slice_ml_predictor"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "20"))
HISTORY_WINDOW_MINUTES = int(os.getenv("HISTORY_WINDOW_MINUTES", "10"))
PREDICT_HORIZON_SECONDS = int(os.getenv("PREDICT_HORIZON_SECONDS", "60"))
ANOMALY_Z_THRESH = float(os.getenv("ANOMALY_Z_THRESH", "2.5"))
MIN_POINTS_FOR_FIT = 4

SLICES = ["embb", "urllc", "mmtc"]


def fit_and_predict(ts, vals, horizon_seconds):
    """Fit linear regression on (time -> count), extrapolate forward."""
    if len(ts) < MIN_POINTS_FOR_FIT:
        return None, None
    t0 = ts[0]
    X = (ts - t0).reshape(-1, 1)
    y = vals
    model = LinearRegression()
    model.fit(X, y)
    future_t = np.array([[ts[-1] - t0 + horizon_seconds]])
    prediction = model.predict(future_t)[0]
    prediction = max(0.0, prediction)  # UE counts can't be negative
    slope = model.coef_[0]
    return prediction, slope


def anomaly_check(vals):
    """Z-score anomaly check on the most recent value vs. recent history."""
    if len(vals) < MIN_POINTS_FOR_FIT:
        return False, 0.0
    recent = vals[:-1]
    mean = np.mean(recent)
    std = np.std(recent)
    if std < 1e-6:
        return False, 0.0
    z = abs(vals[-1] - mean) / std
    return z > ANOMALY_Z_THRESH, z


def push_predictions(predictions):
    registry = CollectorRegistry()
    g_pred = Gauge("slice_ue_count_predicted",
                    f"Predicted UE count {PREDICT_HORIZON_SECONDS}s ahead per slice",
                    ["slice"], registry=registry)
    g_slope = Gauge("slice_ue_count_trend_slope",
                     "Linear trend slope (UEs/sec) per slice",
                     ["slice"], registry=registry)
    g_anomaly = Gauge("slice_traffic_anomaly",
                       "1 if current value is a statistical anomaly vs recent history",
                       ["slice"], registry=registry)
    g_anomaly_z = Gauge("slice_traffic_anomaly_zscore",
                         "Z-score of current value vs recent rolling window",
                         ["slice"], registry=registry)

    for slice_name, (pred, slope, is_anomaly, z) in predictions.items():
        if pred is not None:
            g_pred.labels(slice=slice_name).set(pred)
            g_slope.labels(slice=slice_name).set(slope)
        g_anomaly.labels(slice=slice_name).set(1 if is_anomaly else 0)
        g_anomaly_z.labels(slice=slice_name).set(z)

    push_to_gateway(PUSHGATEWAY_ADDR, job=JOB_NAME, registry=registry)


def main():
    log.info("=" * 55)
    log.info("  Standalone ML Traffic Predictor (read-only)")
    log.info("  Does NOT control auto-scaling -- advisory only")
    log.info("=" * 55)
    log.info(f"  Data source    : {os.getenv('DATA_SOURCE', 'prometheus')}")
    log.info(f"  History window : {HISTORY_WINDOW_MINUTES} min")
    log.info(f"  Predict horizon: {PREDICT_HORIZON_SECONDS}s")
    log.info(f"  Poll interval  : {POLL_INTERVAL}s")
    log.info("=" * 55)

    while True:
        history = get_history("slice_ue_count", HISTORY_WINDOW_MINUTES)
        predictions = {}

        for slice_name in SLICES:
            if slice_name not in history:
                log.warning(f"[{slice_name}] no history available yet")
                predictions[slice_name] = (None, 0.0, False, 0.0)
                continue

            ts, vals = history[slice_name]
            pred, slope = fit_and_predict(ts, vals, PREDICT_HORIZON_SECONDS)
            is_anomaly, z = anomaly_check(vals)
            predictions[slice_name] = (pred, slope or 0.0, is_anomaly, z)

            current = vals[-1] if len(vals) else float("nan")
            pred_str = f"{pred:.1f}" if pred is not None else "N/A"
            trend = "up" if (slope or 0) > 0.01 else ("down" if (slope or 0) < -0.01 else "flat")
            anomaly_str = f" ANOMALY (z={z:.2f})" if is_anomaly else ""
            log.info(f"[{slice_name.upper():6s}] current={current:.0f}  "
                      f"predicted(+{PREDICT_HORIZON_SECONDS}s)={pred_str}  "
                      f"trend={trend}{anomaly_str}")

        push_predictions(predictions)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
