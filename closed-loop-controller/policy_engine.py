#!/usr/bin/env python3
"""
policy_engine.py (Layer 4 -- Policy / Decision Engine)

State (cooldown timestamps + sticky-scaled flags) persists to a small JSON
file next to the metrics DB, so a controller restart doesn't forget it just
scaled a slice up moments ago and re-fire immediately. Persistence is
best-effort: any read/write failure falls back to fresh in-memory state
rather than crashing the controller.
"""

import time
import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from config import CFG


class Action(Enum):
    SCALE_UP = "SCALE_UP"
    SCALE_DOWN = "SCALE_DOWN"
    HOLD = "HOLD"


@dataclass
class Decision:
    slice_name: str
    action: Action
    reason: str


class PolicyEngine:
    def __init__(self):
        self._last_action_ts = {s: 0.0 for s in CFG.SLICES}
        self._scaled = {s: False for s in CFG.SLICES}
        self._state_path = CFG.DB_PATH + ".policy_state.json"
        self._load_state()

    def _load_state(self):
        if not os.path.exists(self._state_path):
            return
        try:
            with open(self._state_path) as f:
                data = json.load(f)
        except Exception:
            return
        for s in CFG.SLICES:
            if s in data.get("last_action_ts", {}):
                self._last_action_ts[s] = data["last_action_ts"][s]
            if s in data.get("scaled", {}):
                self._scaled[s] = data["scaled"][s]

    def _save_state(self):
        payload = {"last_action_ts": self._last_action_ts, "scaled": self._scaled}
        try:
            tmp = self._state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, self._state_path)  # atomic on POSIX
        except Exception:
            pass

    def _in_cooldown(self, slice_name):
        return (time.time() - self._last_action_ts[slice_name]) < CFG.COOLDOWN_S

    def _record_action(self, slice_name):
        self._last_action_ts[slice_name] = time.time()
        self._save_state()

    def _set_scaled(self, slice_name, value):
        self._scaled[slice_name] = value
        self._save_state()

    def decide(self, slice_name: str, a, forecast: Optional[object]) -> Decision:
        up_thresh = CFG.THRESHOLD_UP[slice_name]
        down_thresh = up_thresh * (1 - CFG.THRESHOLD_DOWN_MARGIN)

        # Post-action grace: a UPF rollout-restart makes probes fail transiently (lat=0, loss=100) -- not a new QoS breach.
        if time.time() - self._last_action_ts[slice_name] < 120:
            return Decision(slice_name, Action.HOLD,
                            "post-action grace -- decisions paused for 120s")

        if a.latency_anomaly or a.loss_anomaly:
            if self._scaled[slice_name]:
                return Decision(
                    slice_name, Action.HOLD,
                    f"QoS still degraded (latency={a.latency_ms}ms loss={a.loss_pct}%) "
                    f"but slice already scaled -- repeating won't help, investigate manually",
                )
            self._record_action(slice_name)
            self._set_scaled(slice_name, True)
            return Decision(
                slice_name, Action.SCALE_UP,
                f"QoS guardrail: latency={a.latency_ms}ms loss={a.loss_pct}% "
                f"breaching alert thresholds",
            )

        if self._in_cooldown(slice_name):
            return Decision(slice_name, Action.HOLD,
                             f"in cooldown ({CFG.COOLDOWN_S}s window)")

        if a.ue_count is None:
            return Decision(slice_name, Action.HOLD, "no current ue_count data")

        if self._scaled[slice_name]:
            lv = self.__dict__.setdefault("_lvl", {})
            lvl = lv.get(slice_name, 2)
            max_rep = int(os.getenv("POLICY_MAX_REPLICAS", "4"))
            if lvl < max_rep and a.ue_count >= up_thresh * lvl:
                self._record_action(slice_name)
                lv[slice_name] = lvl + 1
                return Decision(slice_name, Action.SCALE_UP,
                                 f"multi-step: ue_count {a.ue_count} >= {up_thresh * lvl} (level {lvl} -> {lvl + 1}, max {max_rep})")
            if lvl > 2 and a.ue_count <= up_thresh * (lvl - 1) * (1 - CFG.THRESHOLD_DOWN_MARGIN):
                self._record_action(slice_name)
                lv[slice_name] = lvl - 1
                return Decision(slice_name, Action.SCALE_DOWN,
                                 f"multi-step: ue_count {a.ue_count} low (level {lvl} -> {lvl - 1})")
            if lvl <= 2 and a.ue_count <= down_thresh:
                self._record_action(slice_name)
                self._set_scaled(slice_name, False)
                return Decision(slice_name, Action.SCALE_DOWN,
                                 f"reactive: ue_count {a.ue_count} <= down-threshold "
                                 f"{down_thresh:.1f}, reverting scale-up")
            return Decision(slice_name, Action.HOLD,
                             f"already scaled up, ue_count {a.ue_count} still above "
                             f"down-threshold {down_thresh:.1f} -- no change needed")

        if forecast is not None and getattr(forecast, "ok", False):
            will_cross_up = any(v >= up_thresh for v in forecast.forecast_values)
            if will_cross_up and a.ue_count < up_thresh:
                self._record_action(slice_name)
                self._set_scaled(slice_name, True)
                return Decision(
                    slice_name, Action.SCALE_UP,
                    f"predictive: forecast {[round(v,1) for v in forecast.forecast_values]} "
                    f"crosses threshold {up_thresh} before current count does",
                )

        if a.ue_count >= up_thresh:
            self._record_action(slice_name)
            self._set_scaled(slice_name, True)
            return Decision(slice_name, Action.SCALE_UP,
                             f"reactive: ue_count {a.ue_count} >= threshold {up_thresh}")

        return Decision(slice_name, Action.HOLD,
                         f"ue_count {a.ue_count} within band ({down_thresh:.1f}, {up_thresh})")
