#!/usr/bin/env python3
"""
closed_loop_controller.py
Main entrypoint. Ties Layers 2-6 together into one loop:

    analytics.analyze_slice()        (Layer 2 -- NWDAF/analytics)
        -> forecaster.forecast_slice()   (Layer 3 -- ML forecasting)
            -> policy_engine.decide()        (Layer 4 -- decision)
                -> mano_orchestrator.scale_slice()  (Layer 5 -- orchestration)
                    -> verifier.verify_action()         (Layer 6 -- closed-loop check)

Layer 1 (observability) is intentionally NOT in this file -- that's
ue_count_exporter.py + qos_latency_exporter.py + history_writer.py,
already running as their own systemd services. This controller only reads
their output (via analytics.py / forecaster.py) and only writes cluster
state via mano_orchestrator.py. Keeping that read/write boundary is what
makes this safe to run alongside the existing manual debugging workflow.

Run as its own systemd service, separate from the exporters:
    ExecStart=/home/comm/slice-test/slice-env/bin/python3 \\
        /home/comm/slice-test/closed-loop-controller/closed_loop_controller.py

See README.md for the full unit file and OSM-backend setup notes.
"""

import sys
import time

from config import CFG
import metrics_store as store
from analytics import analyze_slice
from forecaster import forecast_slice
from policy_engine import PolicyEngine, Action
from mano_orchestrator import get_backend
from verifier import verify_action


def run_cycle(engine: PolicyEngine, orchestrator):
    for slice_name in CFG.SLICES:
        a = analyze_slice(slice_name)
        fc = forecast_slice(slice_name, metric="ue_count")

        decision = engine.decide(slice_name, a, fc)
        status = (f"[{slice_name:6s}] ue={a.ue_count} lat={a.latency_ms} "
                  f"loss={a.loss_pct} forecast_ok={fc.ok} "
                  f"-> {decision.action.value} ({decision.reason})")
        print(status)

        if decision.action == Action.HOLD:
            continue

        direction = "up" if decision.action == Action.SCALE_UP else "down"
        action_id = orchestrator.scale_slice(slice_name, direction)

        # Fire-and-verify: in production you'd likely do this async
        # (thread/task queue) so one slice's verification wait doesn't
        # block the next slice's decision this cycle. Kept synchronous
        # here for clarity -- see README.md "Next steps" for the async note.
        verify_action(
            slice_name, action_id, direction,
            pre_latency=a.latency_ms, pre_loss=a.loss_pct,
            orchestrator=orchestrator,
        )


def main():
    store.init_db()
    engine = PolicyEngine()
    orchestrator = get_backend()
    print(f"closed_loop_controller: backend={CFG.MANO_BACKEND}, "
          f"poll every {CFG.CONTROLLER_POLL_INTERVAL_S}s, "
          f"slices={CFG.SLICES}")

    try:
        while True:
            run_cycle(engine, orchestrator)
            time.sleep(CFG.CONTROLLER_POLL_INTERVAL_S)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
