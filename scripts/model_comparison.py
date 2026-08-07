#!/usr/bin/env python3
"""
model_comparison.py (P3 production-readiness item)

Compares the current predictor (simple linear regression over a fixed
history window) against Holt's linear trend method (double exponential
smoothing) on the SAME historical data, to see whether a richer model
genuinely improves accuracy for this testbed's traffic patterns.

WHY HOLT'S METHOD, NOT PROPHET/ARIMA
--------------------------------------
Prophet and full ARIMA require additional dependencies (statsmodels,
prophet) that could not be reliably installed on the target machine
(no internet access confirmed earlier in this session -- a prior
`pip install pyyaml` failed with a DNS resolution error). Holt's linear
trend method needs only numpy (already a dependency of ml_predictor.py)
and is implemented from scratch below in ~20 lines. It is a legitimate,
well-established forecasting technique (exponential smoothing with a
trend component), not a toy method -- it's what many production
forecasting systems use before reaching for something heavier.

METHOD
------
Linear regression (current): fits a single line to ALL points in the
history window equally, extrapolates. Sensitive to how much old/flat
data is mixed with a new trend inside the fixed window (this is exactly
the failure mode documented in evidence/ml_accuracy_summary.md).

Holt's linear trend (new): maintains a "level" and a "trend" estimate
that are updated with EXPONENTIALLY DECAYING weight -- recent
observations matter more than old ones automatically, without needing
to pick a hard window-size cutoff. Two smoothing parameters:
  alpha (level smoothing): higher = more responsive to recent level
  beta  (trend smoothing):  higher = more responsive to recent trend changes

This does NOT modify ml_predictor.py or touch the live cluster --
it's a standalone comparison against already-collected CSV data, run
locally by a human, to inform which model (if either) should become
the default. See evidence/ for the datasets this was run against.
"""

import argparse
import csv
from collections import defaultdict

import numpy as np
from sklearn.linear_model import LinearRegression

MIN_POINTS_FOR_FIT = 4


def load_csv(path, ts_col="timestamp", entity_col="entity", value_col="value"):
    by_entity = defaultdict(list)
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = float(row[ts_col])
                entity = row[entity_col]
                value = float(row[value_col])
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
    """Identical to ml_predictor.py's fit_and_predict()."""
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
    """
    Holt's linear trend method (double exponential smoothing).
    Assumes roughly-evenly-spaced samples; uses the median inter-sample
    gap as the step size for projecting `horizon_seconds` ahead in units
    of "number of steps", which is the standard way Holt's method
    reasons about the future (steps, not raw seconds).
    """
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


def lag_corrected_mae(ts, vals, predict_fn, horizon_seconds, **kwargs):
    """
    Walk forward through the series (same evaluation harness as
    ml_predictor_batch.py's evaluate_entity + compute_lag_corrected_mae),
    compute predicted[T] using only data up to T, compare against the
    actual value closest to T+horizon_seconds.
    """
    errors = []
    for i in range(MIN_POINTS_FOR_FIT - 1, len(ts)):
        window_ts = ts[: i + 1]
        window_vals = vals[: i + 1]
        pred = predict_fn(window_ts, window_vals, horizon_seconds, **kwargs)
        if pred is None:
            continue
        target_t = ts[i] + horizon_seconds
        future_mask = ts >= target_t
        if not future_mask.any():
            continue
        future_idx = np.argmax(future_mask)
        actual_future = vals[future_idx]
        errors.append(abs(actual_future - pred))
    if not errors:
        return None, None, 0
    return sum(errors) / len(errors), max(errors), len(errors)


def main():
    parser = argparse.ArgumentParser(description="Compare linear regression vs Holt's method")
    parser.add_argument("--input", required=True, action="append",
                         help="CSV file(s) to evaluate against (can pass multiple)")
    parser.add_argument("--horizon-seconds", type=float, default=60)
    parser.add_argument("--alpha", type=float, default=0.5, help="Holt's level smoothing")
    parser.add_argument("--beta", type=float, default=0.3, help="Holt's trend smoothing")
    args = parser.parse_args()

    print("=" * 70)
    print("  Model comparison: Linear Regression vs Holt's Linear Trend")
    print(f"  Horizon: {args.horizon_seconds:.0f}s | Holt alpha={args.alpha} beta={args.beta}")
    print("=" * 70)

    lr_all_errors = []
    holt_all_errors = []

    for path in args.input:
        print(f"\n--- {path} ---")
        dataset = load_csv(path)
        for entity, (ts, vals) in dataset.items():
            lr_mae, lr_max, lr_n = lag_corrected_mae(
                ts, vals, predict_linear_regression, args.horizon_seconds)
            holt_mae, holt_max, holt_n = lag_corrected_mae(
                ts, vals, predict_holt_linear, args.horizon_seconds,
                alpha=args.alpha, beta=args.beta)

            if lr_mae is not None:
                lr_all_errors.append((lr_mae, lr_n))
            if holt_mae is not None:
                holt_all_errors.append((holt_mae, holt_n))

            lr_str = f"{lr_mae:.3f} (N={lr_n})" if lr_mae is not None else "N/A"
            holt_str = f"{holt_mae:.3f} (N={holt_n})" if holt_mae is not None else "N/A"
            winner = ""
            if lr_mae is not None and holt_mae is not None:
                winner = " <- Holt better" if holt_mae < lr_mae else (
                    " <- LR better" if lr_mae < holt_mae else " <- tie")
            print(f"  {entity:10s} LinearRegression MAE={lr_str:20s} Holt MAE={holt_str}{winner}")

    def weighted_avg(pairs):
        total_err_weighted = sum(m * n for m, n in pairs)
        total_n = sum(n for _, n in pairs)
        return total_err_weighted / total_n if total_n else None

    print("\n" + "=" * 70)
    print("  OVERALL (N-weighted average across all entities/files)")
    lr_overall = weighted_avg(lr_all_errors)
    holt_overall = weighted_avg(holt_all_errors)
    print(f"  Linear Regression: {lr_overall:.3f}" if lr_overall else "  Linear Regression: N/A")
    print(f"  Holt's Linear Trend: {holt_overall:.3f}" if holt_overall else "  Holt's Linear Trend: N/A")
    if lr_overall and holt_overall:
        pct = (lr_overall - holt_overall) / lr_overall * 100
        if pct > 0:
            print(f"  -> Holt's method reduces MAE by {pct:.1f}%")
        else:
            print(f"  -> Linear regression reduces MAE by {-pct:.1f}%")
    print("=" * 70)


if __name__ == "__main__":
    main()
