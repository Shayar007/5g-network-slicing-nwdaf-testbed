#!/usr/bin/env python3
"""
config.py
Central, env-driven configuration for the closed-loop auto-scaling pipeline.

Layout mirrors the existing ue_count_exporter.py / qos_latency_exporter.py
convention: everything overridable via os.getenv, sane defaults for the
current single-host testbed.

This file has NO side effects (no network/DB calls) so every other module
can safely `from config import CFG` without ordering issues.
"""

import os


def _int(name, default):
    return int(os.getenv(name, str(default)))


def _float(name, default):
    return float(os.getenv(name, str(default)))


class Config:
    # ---- Prometheus (existing pushgateway-forward tunnel, port 9090) ----
    PROM_URL = os.getenv("PROM_URL", "http://localhost:9090")

    # ---- Local time-series store (bridges Prometheus -> forecaster) ----
    DB_PATH = os.getenv("CLOSED_LOOP_DB", "/home/comm/slice-test/closed_loop_history.db")

    # ---- Slices this pipeline manages. Must match Prometheus labels
    #      produced by ue_count_exporter.py / qos_latency_exporter.py ----
    SLICES = ["embb", "urllc", "mmtc"]

    # UPF deployment name per slice (from ue_count_exporter.py's
    # UPF_NAME_OVERRIDES -- kept identical here so both stay in sync)
    UPF_NAME = {
        "embb": "oai-upf",
        "urllc": "oai-upf2",
        "mmtc": "oai-upf-auto-auto01",
    }

    # ---- Polling / history ----
    HISTORY_POLL_INTERVAL_S = _int("HISTORY_POLL_INTERVAL_S", 15)
    CONTROLLER_POLL_INTERVAL_S = _int("CONTROLLER_POLL_INTERVAL_S", 30)

    # Minimum samples before the forecaster is trusted at all. Below this,
    # policy_engine falls back to reactive (current-value) thresholds only --
    # this is the fix for the "Holt gives bad accuracy" issue: n=5 samples
    # is not enough, we need real history depth here, not more UEs.
    MIN_FORECAST_SAMPLES = _int("MIN_FORECAST_SAMPLES", 30)
    FORECAST_LOOKBACK_S = _int("FORECAST_LOOKBACK_S", 3600)  # 1h of history
    FORECAST_STEPS_AHEAD = _int("FORECAST_STEPS_AHEAD", 3)   # ~3 polls ahead

    # Holt's linear trend smoothing params (manual impl, no statsmodels --
    # matches the repo's existing note that statsmodels is unavailable
    # offline). Tune alpha/beta if forecasts over/under-react.
    HOLT_ALPHA = _float("HOLT_ALPHA", 0.5)
    HOLT_BETA = _float("HOLT_BETA", 0.3)

    # ---- Policy thresholds (per-slice UE count), with hysteresis so we
    #      don't flap: scale-down only triggers well below scale-up ----
    THRESHOLD_UP = {
        "embb": _int("EMBB_THRESHOLD_UP", 10),
        "urllc": _int("URLLC_THRESHOLD_UP", 3),
        "mmtc": _int("MMTC_THRESHOLD_UP", 5),
    }
    THRESHOLD_DOWN_MARGIN = _float("THRESHOLD_DOWN_MARGIN", 0.4)  # 40% below UP

    # QoS guardrails: even if UE count is fine, bad QoS forces a scale-up
    LATENCY_ALERT_MS = _float("LATENCY_ALERT_MS", 20.0)
    LOSS_ALERT_PCT = _float("LOSS_ALERT_PCT", 5.0)

    COOLDOWN_S = _int("ACTION_COOLDOWN_S", 120)

    # ---- Orchestration backend ----
    MANO_BACKEND = os.getenv("MANO_BACKEND", "kubectl")  # "kubectl" | "osm"
    K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", "oai")

    OSM_NBI_URL = os.getenv("OSM_NBI_URL", "http://localhost:9999/osm")
    OSM_USER = os.getenv("OSM_USER", "admin")
    OSM_PASSWORD = os.getenv("OSM_PASSWORD", "")
    OSM_PROJECT = os.getenv("OSM_PROJECT", "admin")
    OSM_NS_NAME = {  # NS instance name per slice in OSM, if managed there
        "embb": os.getenv("OSM_NS_EMBB", ""),
        "urllc": os.getenv("OSM_NS_URLLC", ""),
        "mmtc": os.getenv("OSM_NS_MMTC", ""),
    }

    # ---- Closed-loop verification ----
    VERIFY_WAIT_S = _int("VERIFY_WAIT_S", 30)
    VERIFY_IMPROVEMENT_MARGIN_PCT = _float("VERIFY_IMPROVEMENT_MARGIN_PCT", 5.0)

    LOG_PATH = os.getenv("CLOSED_LOOP_LOG", "/var/log/slice-testbed/closed_loop_controller.log")


CFG = Config()
