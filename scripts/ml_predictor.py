#!/usr/bin/env python3
"""
ml_predictor.py
Standalone, read-only ML traffic-prediction service. Does NOT control
anything -- it only reads historical UE-count metrics from Prometheus,
trains a lightweight regression model per slice, and pushes predicted
near-future UE counts as new Prometheus metrics for visualization.

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

Usage:
  python3 ml_predictor.py
Requires: numpy, scikit-learn, requests, prometheus_client
  pip install numpy scikit-learn requests prometheus_client --break-system-packages
"""

import os
import time
import logging
from datetime import datetime

import numpy as np
import requests
from sklearn.linear_model import LinearRegression
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [ml-predictor] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
PUSHGATEWAY_ADDR = os.getenv("PUSHGATEWAY_ADDR", "localhost:9091")
JOB_NAME = "oai_slice_ml_predictor"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "20"))
HISTORY_WINDOW_MINUTES = int(os.getenv("HISTORY_WINDOW_MINUTES", "10"))
PREDICT_HORIZON_SECONDS = int(os.getenv("PREDICT_HORIZON_SECONDS", "60"))
ANOMALY_Z_THRESH = float(os.getenv("ANOMALY_Z_THRESH", "2.5"))
MIN_POINTS_FOR_FIT = 4

SLICES = ["embb", "urllc", "mmtc"]


def query_range(metric, minutes):
    """Query Prometheus for a metric's history over the last `minutes`."""
    end = time.time()
    start = end - minutes * 60
    step = "15s"
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params={"query": metric, "start": start, "end": end, "step": step},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        results = {}
        for series in data.get("data", {}).get("result", []):
            slice_label = series["metric"].get("slice", "unknown")
            values = series.get("values", [])
            ts = np.array([float(v[0]) for v in values])
            vals = np.array([float(v[1]) for v in values])
            results[slice_label] = (ts, vals)
        return results
    except Exception as e:
        log.warning(f"Prometheus query failed: {e}")
        return {}


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
    log.info(f"  History window : {HISTORY_WINDOW_MINUTES} min")
    log.info(f"  Predict horizon: {PREDICT_HORIZON_SECONDS}s")
    log.info(f"  Poll interval  : {POLL_INTERVAL}s")
    log.info("=" * 55)

    while True:
        history = query_range("slice_ue_count", HISTORY_WINDOW_MINUTES)
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
