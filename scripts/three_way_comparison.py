#!/usr/bin/env python3
"""
three_way_comparison.py
Extends model_comparison.py's evaluation harness (identical lag-corrected
MAE methodology) to include RandomForestRegressor alongside the existing
LinearRegression and Holt's Linear Trend comparison, on the SAME CSV
evidence files, for a fair three-way decision.

CAVEAT (stated up front, not buried): both evidence CSVs contain only
11 data points each. Random Forest's usual advantage (learning nonlinear
patterns from many examples) is undermined by this small N -- fewer than
5 training examples are available per prediction. This comparison is run
and reported honestly regardless of which way it comes out.
"""
import csv
import sys
from collections import defaultdict
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor

MIN_POINTS_FOR_FIT = 4


def load_csv(path):
    by_entity = defaultdict(list)
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = float(row["timestamp"])
                entity = row["entity"]
                value = float(row["value"])
            except (KeyError, ValueError):
                continue
            by_entity[entity].append((ts, value))
    result = {}
    for entity, points in by_entity.items():
        points.sort(key=lambda p: p[0])
        ts = np.array([p[0] for p in points])
        vals = np.array([p[1] for p in points])
        result[entity] = (ts, vals)
    return result


def predict_linear_regression(ts, vals, horizon_seconds):
    if len(ts) < MIN_POINTS_FOR_FIT:
        return None
    t0 = ts[0]
    X = (ts - t0).reshape(-1, 1)
    model = LinearRegression()
    model.fit(X, vals)
    future_t = np.array([[ts[-1] - t0 + horizon_seconds]])
    pred = model.predict(future_t)[0]
    return max(0.0, pred)


def predict_holt_linear(ts, vals, horizon_seconds, alpha=0.5, beta=0.3):
    if len(vals) < MIN_POINTS_FOR_FIT:
        return None
    level = vals[0]
    trend = vals[1] - vals[0]
    for i in range(1, len(vals)):
        prev_level = level
        level = alpha * vals[i] + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
    diffs = np.diff(ts)
    step = np.median(diffs) if len(diffs) else 1.0
    if step <= 0:
        step = 1.0
    steps_ahead = horizon_seconds / step
    pred = level + steps_ahead * trend
    return max(0.0, pred)


def build_rf_features(ts, vals):
    if len(vals) < 4:
        return None
    current = vals[-1]
    roc_short = (vals[-1] - vals[-3]) / 3 if len(vals) >= 3 else 0.0
    roc_long = (vals[-1] - vals[-6]) / 6 if len(vals) >= 6 else roc_short
    volatility = float(np.std(vals[-6:])) if len(vals) >= 2 else 0.0
    tod_seconds = ts[-1] % 86400
    tod_sin = np.sin(2 * np.pi * tod_seconds / 86400)
    tod_cos = np.cos(2 * np.pi * tod_seconds / 86400)
    return np.array([current, roc_short, roc_long, volatility, tod_sin, tod_cos])


def predict_random_forest(ts, vals, horizon_seconds):
    n = len(vals)
    if n < MIN_POINTS_FOR_FIT:
        return None
    X, y = [], []
    for i in range(3, n):
        target_ts = ts[i] + horizon_seconds
        future_idx = np.searchsorted(ts, target_ts)
        if future_idx >= n or future_idx <= i:
            continue
        feat = build_rf_features(ts[: i + 1], vals[: i + 1])
        if feat is None:
            continue
        X.append(feat)
        y.append(vals[future_idx])
    if len(X) < 3:
        return None  # honestly insufficient data, do not fabricate a prediction
    model = RandomForestRegressor(n_estimators=30, max_depth=3, random_state=42, n_jobs=1)
    model.fit(np.array(X), np.array(y))
    current_feat = build_rf_features(ts, vals)
    if current_feat is None:
        return None
    pred = model.predict(current_feat.reshape(1, -1))[0]
    return max(0.0, pred)


def lag_corrected_mae(ts, vals, predict_fn, horizon_seconds):
    errors = []
    n_skipped_no_pred = 0
    for i in range(MIN_POINTS_FOR_FIT - 1, len(ts)):
        hist_ts = ts[: i + 1]
        hist_vals = vals[: i + 1]
        pred = predict_fn(hist_ts, hist_vals, horizon_seconds)
        if pred is None:
            n_skipped_no_pred += 1
            continue
        target_ts = ts[i] + horizon_seconds
        future_idx = np.searchsorted(ts, target_ts)
        if future_idx >= len(ts):
            continue
        actual = vals[future_idx]
        errors.append(abs(pred - actual))
    if not errors:
        return None, 0, n_skipped_no_pred
    return float(np.mean(errors)), len(errors), n_skipped_no_pred


def main():
    files = sys.argv[1:]
    if not files:
        print("Usage: three_way_comparison.py <csv1> [csv2] ...")
        sys.exit(1)

    horizon = 60
    lr_all, holt_all, rf_all = [], [], []

    for path in files:
        print(f"\n=== {path} ===")
        data = load_csv(path)
        for entity, (ts, vals) in data.items():
            lr_mae, lr_n, lr_skip = lag_corrected_mae(ts, vals, predict_linear_regression, horizon)
            holt_mae, holt_n, holt_skip = lag_corrected_mae(ts, vals, predict_holt_linear, horizon)
            rf_mae, rf_n, rf_skip = lag_corrected_mae(ts, vals, predict_random_forest, horizon)

            def fmt(m, n, skip):
                return f"{m:.3f} (n={n}, skipped={skip})" if m is not None else f"N/A (skipped={skip}, insufficient data)"

            print(f"  {entity:10s}")
            print(f"    LinearRegression : {fmt(lr_mae, lr_n, lr_skip)}")
            print(f"    Holt's Linear    : {fmt(holt_mae, holt_n, holt_skip)}")
            print(f"    Random Forest    : {fmt(rf_mae, rf_n, rf_skip)}")

            if lr_mae is not None:
                lr_all.append((lr_mae, lr_n))
            if holt_mae is not None:
                holt_all.append((holt_mae, holt_n))
            if rf_mae is not None:
                rf_all.append((rf_mae, rf_n))

    def weighted_avg(pairs):
        total_n = sum(n for _, n in pairs)
        if not total_n:
            return None
        return sum(m * n for m, n in pairs) / total_n

    print("\n" + "=" * 70)
    print("  OVERALL (N-weighted average)")
    lr_overall = weighted_avg(lr_all)
    holt_overall = weighted_avg(holt_all)
    rf_overall = weighted_avg(rf_all)
    print(f"  Linear Regression : {lr_overall:.3f}" if lr_overall is not None else "  Linear Regression : N/A")
    print(f"  Holt's Linear     : {holt_overall:.3f}" if holt_overall is not None else "  Holt's Linear     : N/A")
    print(f"  Random Forest     : {rf_overall:.3f}" if rf_overall is not None else "  Random Forest     : N/A (insufficient data across all entities)")
    print("=" * 70)

    results = {"LinearRegression": lr_overall, "Holt": holt_overall, "RandomForest": rf_overall}
    valid = {k: v for k, v in results.items() if v is not None}
    if valid:
        best = min(valid, key=valid.get)
        print(f"\n  BEST (lowest MAE, among models with sufficient data): {best} ({valid[best]:.3f})")
    else:
        print("\n  No model had sufficient data for a valid comparison.")


if __name__ == "__main__":
    main()
