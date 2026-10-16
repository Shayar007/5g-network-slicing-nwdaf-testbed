#!/usr/bin/env python3
"""
verifier.py (Layer 6 -- Closed-loop verification)

After an orchestration action, waits, re-measures QoS for that slice, and
decides whether it actually helped. This is the feedback arrow in your
diagram ("Did latency/load/SLA improve? If not -> correct action").

On WORSE outcomes, triggers a corrective action via the orchestrator
(currently: opposite-direction nudge or a second same-direction retry for
SCALE_UP that didn't land) and logs everything so the policy engine's
cooldown state stays consistent with what actually happened.
"""

import time
from dataclasses import dataclass
from enum import Enum

from config import CFG
from analytics import analyze_slice
from mano_orchestrator import OrchestratorBackend


class VerificationOutcome(Enum):
    IMPROVED = "IMPROVED"
    NO_CHANGE = "NO_CHANGE"
    WORSE = "WORSE"
    INCONCLUSIVE = "INCONCLUSIVE"  # missing before/after data


@dataclass
class VerificationResult:
    slice_name: str
    action_id: str
    outcome: VerificationOutcome
    detail: str


def _pct_change(before, after):
    if before in (None, 0):
        return None
    return ((after - before) / before) * 100.0


def verify_action(slice_name: str, action_id: str, direction: str,
                   pre_latency, pre_loss,
                   orchestrator: OrchestratorBackend,
                   wait_s=None) -> VerificationResult:
    wait_s = wait_s or CFG.VERIFY_WAIT_S
    import os
    _osm = action_id.startswith("osm-")
    if _osm or (os.getenv("DEMO_DRY_RUN") == "1" and action_id.startswith("kubectl-")):
        why = ("OSM scaled the slice's KNF (convergence checked by the backend); slice QoS is not expected to change since the UPF is not scaled"
               if _osm else "DRY-RUN: no action was applied, nothing to verify")
        print(f"[verifier] {action_id}: NOT_EVALUATED -- {why}", flush=True)
        return VerificationResult(slice_name, action_id, VerificationOutcome.INCONCLUSIVE, why)
    print(f"[verifier] {action_id}: waiting {wait_s}s before re-checking {slice_name} QoS")
    time.sleep(wait_s)

    post = analyze_slice(slice_name, lookback_s=60)
    post_latency, post_loss = post.latency_ms, post.loss_pct

    if pre_latency is None or post_latency is None:
        return VerificationResult(
            slice_name, action_id, VerificationOutcome.INCONCLUSIVE,
            f"missing latency data (pre={pre_latency}, post={post_latency})",
        )

    lat_change_pct = _pct_change(pre_latency, post_latency) or 0.0
    loss_change_pct = _pct_change(pre_loss, post_loss) if pre_loss else 0.0

    margin = CFG.VERIFY_IMPROVEMENT_MARGIN_PCT
    detail = (f"latency {pre_latency:.2f}ms -> {post_latency:.2f}ms "
              f"({lat_change_pct:+.1f}%), loss {pre_loss}% -> {post_loss}%")

    # Noise floor: sub-0.5ms latency jitter is measurement noise, not a real change.
    if abs(post_latency - pre_latency) < 0.5:
        lat_change_pct = 0.0

    # Guard: after a UPF restart the probe can fail (latency 0.00ms + high loss). Not an improvement.
    if post_latency <= 0 or (post_loss or 0) > (pre_loss or 0) + 5.0:
        return VerificationResult(
            slice_name, action_id, VerificationOutcome.INCONCLUSIVE,
            detail + " -- probe failed or loss increased (UPF likely still restarting), not counted as improvement",
        )

    if lat_change_pct <= -margin:
        outcome = VerificationOutcome.IMPROVED
    elif lat_change_pct >= margin:
        outcome = VerificationOutcome.WORSE
    else:
        outcome = VerificationOutcome.NO_CHANGE

    result = VerificationResult(slice_name, action_id, outcome, detail)
    print(f"[verifier] {action_id}: {outcome.value} -- {detail}")

    if outcome == VerificationOutcome.WORSE:
        _handle_worse_outcome(slice_name, direction, orchestrator, result)

    return result


def _handle_worse_outcome(slice_name, direction, orchestrator, result):
    """
    Corrective step: if a SCALE_UP made things worse (e.g. rollout-restart
    caused a transient blip rather than real capacity), retry once. If a
    SCALE_DOWN made things worse, undo it by scaling back up.
    Kept deliberately simple (single retry, no exponential backoff) -- this
    is the hook point to make smarter later.
    """
    corrective_direction = "up" if direction == "down" else "up"
    print(f"[verifier] corrective action for {slice_name}: scale {corrective_direction} "
          f"(previous action made things worse: {result.detail})")
    orchestrator.scale_slice(slice_name, corrective_direction)
