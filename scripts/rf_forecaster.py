#!/usr/bin/env python3
"""
rf_forecaster.py
Random Forest based multi-feature traffic forecaster -- upgrade over the
single-feature LinearRegression baseline (ml_predictor.py) and the
Holt's Exponential Smoothing comparison (model_comparison.py).

WHY Random Forest (vs LinearRegression):
- Captures NON-LINEAR patterns in UE-count trends (bursts, plateaus,
  sudden drops) that a straight-line fit cannot represent.
- Uses MULTIPLE features (not just raw time), letting the model learn
  from recent rate-of-change, recent volatility, and time-of-day,
  rather than only extrapolating a single slope.
- Still fast enough for a 20s control-loop (training on a small rolling
  window is sub-second; inference is near-instant).
- Feature importances are directly inspectable -- this preserves the
  interpretability argument made for LinearRegression, while adding
  real modeling capacity.

Features per prediction:
  - current value
  - rate of change over last 3 samples
  - rate of change over last 6 samples
  - rolling std-dev (volatility) over the window
  - time-of-day (seconds since midnight, cyclically encoded)

This is designed as a drop-in ALTERNATIVE predictor, not a replacement
of the existing pipeline -- run it side-by-side against
ml_predictor.py's LinearRegression output to produce a direct,
apples-to-apples accuracy comparison (extending model_comparison.py).
"""

import numpy as np
from sklearn.ensemble import RandomForestRegressor


def build_features(timestamps: np.ndarray, values: np.ndarray):
    """
    Given a rolling window of (timestamp, value) pairs, build a single
    feature vector describing the CURRENT state -- used both for
    training targets (features at time T -> actual value at T+horizon)
    and for making a live prediction from the most recent window.
    """
    if len(values) < 4:
        return None

    current = values[-1]
    roc_short = (values[-1] - values[-3]) / 3 if len(values) >= 3 else 0.0
    roc_long = (values[-1] - values[-6]) / 6 if len(values) >= 6 else roc_short
    volatility = float(np.std(values[-6:])) if len(values) >= 2 else 0.0

    import time as _time
    tod_seconds = timestamps[-1] % 86400
    tod_sin = np.sin(2 * np.pi * tod_seconds / 86400)
    tod_cos = np.cos(2 * np.pi * tod_seconds / 86400)

    return np.array([current, roc_short, roc_long, volatility, tod_sin, tod_cos])


def fit_and_predict_rf(timestamps: np.ndarray, values: np.ndarray, horizon_s: int = 60):
    """
    Trains a small RandomForestRegressor on a sliding-window
    reconstruction of the history (each point in history becomes one
    training example: features-at-time-T -> value-at-time-T+horizon,
    using only data that would have been available at time T), then
    predicts horizon_s seconds ahead of the most recent point.

    Returns None if there isn't enough history to build at least a
    handful of training examples.
    """
    n = len(values)
    if n < 10:
        return None

    X, y = [], []
    for i in range(6, n):
        # Find the target: the value at approximately timestamps[i] + horizon_s
        target_ts = timestamps[i] + horizon_s
        future_idx = np.searchsorted(timestamps, target_ts)
        if future_idx >= n:
            continue
        feat = build_features(timestamps[: i + 1], values[: i + 1])
        if feat is None:
            continue
        X.append(feat)
        y.append(values[future_idx])

    if len(X) < 5:
        return None

    X = np.array(X)
    y = np.array(y)

    model = RandomForestRegressor(
        n_estimators=50, max_depth=4, random_state=42, n_jobs=1
    )
    model.fit(X, y)

    current_feat = build_features(timestamps, values)
    if current_feat is None:
        return None
    pred = model.predict(current_feat.reshape(1, -1))[0]
    pred = max(0.0, pred)

    feature_names = ["current", "roc_short", "roc_long", "volatility", "tod_sin", "tod_cos"]
    importances = dict(zip(feature_names, model.feature_importances_))

    return pred, importances


if __name__ == "__main__":
    # Self-test with synthetic ramping data
    ts = np.arange(0, 600, 15).astype(float)
    vals = np.clip(4 + 0.02 * ts + np.random.normal(0, 0.3, len(ts)), 0, None)
    result = fit_and_predict_rf(ts, vals, horizon_s=60)
    if result:
        pred, importances = result
        print(f"Predicted (+60s): {pred:.2f}")
        print("Feature importances:")
        for k, v in sorted(importances.items(), key=lambda x: -x[1]):
            print(f"  {k}: {v:.3f}")
    else:
        print("Not enough data for a prediction (expected with this small synthetic set)")
