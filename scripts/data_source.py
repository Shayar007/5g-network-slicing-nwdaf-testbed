#!/usr/bin/env python3
"""
data_source.py
Pluggable data-source abstraction for ml_predictor.py (and any future
consumer). Real operator data (Airtel/Jio/Vi or similar) will most
realistically arrive as an offline historical export -- PM counters,
KPI logs, or xDR-derived aggregates -- NOT as a live Prometheus feed,
since operators do not typically grant live production API access to
external research testbeds.

This module lets the same forecasting code run against either source by
swapping DATA_SOURCE, with zero changes to the model-fitting logic.

DATA_SOURCE=prometheus (default): current live-testbed behavior.
DATA_SOURCE=csv: reads historical UE-count time series from a CSV file.
    Expected columns: timestamp (unix epoch seconds), slice, value
    Example row: 1785817363,mmtc,11

Usage:
    from data_source import get_history
    history = get_history("slice_ue_count", minutes=10)
    # returns {slice_name: (timestamps_array, values_array)} regardless
    # of which underlying source was configured.
"""

import os
import csv
import time
import numpy as np

DATA_SOURCE = os.getenv("DATA_SOURCE", "prometheus").lower()
CSV_PATH = os.getenv("HISTORICAL_DATA_CSV", "/data/historical_ue_counts.csv")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")


def _get_history_prometheus(metric, minutes):
    import requests
    end = time.time()
    start = end - minutes * 60
    step = "15s"
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


def _get_history_csv(minutes, csv_path=None):
    """
    Reads a CSV historical export instead of querying live Prometheus.
    Only rows within the trailing `minutes` window (relative to the
    latest timestamp present in the file, NOT wall-clock time -- this
    matters for replaying a historical dataset that isn't from "now")
    are returned.
    """
    path = csv_path or CSV_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"HISTORICAL_DATA_CSV not found at {path}. Expected columns: "
            f"timestamp,slice,value"
        )

    rows_by_slice = {}
    max_ts = 0
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = float(row["timestamp"])
            slice_name = row["slice"]
            value = float(row["value"])
            rows_by_slice.setdefault(slice_name, []).append((ts, value))
            max_ts = max(max_ts, ts)

    cutoff = max_ts - minutes * 60
    results = {}
    for slice_name, rows in rows_by_slice.items():
        rows = sorted([r for r in rows if r[0] >= cutoff], key=lambda r: r[0])
        if not rows:
            continue
        ts_arr = np.array([r[0] for r in rows])
        val_arr = np.array([r[1] for r in rows])
        results[slice_name] = (ts_arr, val_arr)
    return results


def get_history(metric, minutes):
    """
    Unified entry point. Returns {slice_name: (timestamps, values)}
    regardless of DATA_SOURCE. Callers should not need to know or care
    which underlying source is active.
    """
    if DATA_SOURCE == "csv":
        return _get_history_csv(minutes)
    elif DATA_SOURCE == "prometheus":
        return _get_history_prometheus(metric, minutes)
    else:
        raise ValueError(f"Unknown DATA_SOURCE={DATA_SOURCE!r}, expected 'prometheus' or 'csv'")


if __name__ == "__main__":
    print(f"DATA_SOURCE={DATA_SOURCE}")
    try:
        h = get_history("slice_ue_count", minutes=10)
        for slice_name, (ts, vals) in h.items():
            print(f"  {slice_name}: {len(ts)} points, latest={vals[-1] if len(vals) else 'N/A'}")
    except Exception as e:
        print(f"  (no data / source unavailable in this test context: {e})")
