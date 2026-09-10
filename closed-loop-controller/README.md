# Closed-Loop Controller Skeleton

Wires your existing testbed into the full 6-layer loop:

```
Layer 1  Observability        ue_count_exporter.py + qos_latency_exporter.py   (ALREADY RUNNING, untouched)
Layer 1->2 bridge              history_writer.py     (NEW -- reads Prometheus, builds real history)
Layer 2  NWDAF/Analytics       analytics.py           (NEW)
Layer 3  ML Forecasting        forecaster.py          (NEW -- manual Holt's, no statsmodels)
Layer 4  Policy/Decision       policy_engine.py       (NEW -- hysteresis + cooldown + QoS guardrail)
Layer 5  MANO/Orchestration    mano_orchestrator.py   (NEW -- kubectl backend now, OSM backend stubbed)
Layer 6  Closed-loop verify    verifier.py            (NEW -- post-action QoS re-check + corrective retry)
Main loop                      closed_loop_controller.py
```

## Why this design

- **Read/write boundary is strict.** Everything up to and including
  `policy_engine.py` only *reads* (Prometheus, then local SQLite).
  Only `mano_orchestrator.py` mutates cluster state. This mirrors the
  "standalone, read-only exporter" pattern already used in
  `ue_count_exporter.py` / `qos_latency_exporter.py`, so this can run
  alongside your existing manual debugging without risk of double-acting.
- **The Holt accuracy problem is fixed here, not by adding UEs.**
  `three_way_comparison.py` was scoring Holt on `n=5` samples.
  `forecaster.py` refuses to forecast below `MIN_FORECAST_SAMPLES` (30 by
  default) and tells `policy_engine.py` so explicitly -- which then falls
  back to pure reactive thresholds until `history_writer.py` has built up
  enough real history. Let `history_writer.py` run for at least an hour
  before expecting good forecasts.
- **OSM is already deployed and idle.** `kubectl get pods -n osm` showed
  `lcm`, `ro`, `nbi`, `mon` all Running. `mano_orchestrator.py` ships a
  working `KubectlBackend` (matches what you do manually today) and a
  `OSMBackend` stub with the exact TODOs needed to point it at your real
  NS instances -- see the docstring in that file.

## Deploy

```bash
# 1. Copy this whole folder onto the testbed host
cp -r closed-loop-controller ~/slice-test/

cd ~/slice-test/closed-loop-controller
source ../slice-env/bin/activate
pip install requests --break-system-packages   # only new dependency

# 2. Sanity check config (adjust thresholds/env vars as needed)
python3 -c "from config import CFG; print(CFG.SLICES, CFG.THRESHOLD_UP)"

# 3. Start the history bridge FIRST, let it run a while before the controller
python3 history_writer.py &

# wait >= 1h so forecaster.py has enough samples, then:
python3 closed_loop_controller.py
```

## systemd units (once validated manually)

`/etc/systemd/system/closed-loop-history.service`
```ini
[Unit]
Description=Closed-loop history bridge (Prometheus -> local SQLite)
After=network.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=/home/comm/slice-test/closed-loop-controller
ExecStart=/home/comm/slice-test/slice-env/bin/python3 history_writer.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/closed-loop-controller.service`
```ini
[Unit]
Description=Closed-loop auto-scaling controller
After=closed-loop-history.service
Requires=closed-loop-history.service

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
Environment=MANO_BACKEND=kubectl
WorkingDirectory=/home/comm/slice-test/closed-loop-controller
ExecStart=/home/comm/slice-test/slice-env/bin/python3 closed_loop_controller.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now closed-loop-history.service
# wait for history to accumulate, then:
sudo systemctl enable --now closed-loop-controller.service
```

## Switching to OSM orchestration

1. `kubectl port-forward -n osm svc/nbi 9999:9999` (or set up proper ingress)
2. `osm ns-list` (or NBI `GET /osm/nslcm/v1/ns_instances`) to get real NS ids
3. Set env vars: `MANO_BACKEND=osm`, `OSM_USER`, `OSM_PASSWORD`, `OSM_PROJECT`,
   `OSM_NS_EMBB` / `OSM_NS_URLLC` / `OSM_NS_MMTC`
4. Fill in the `scaling-group-descriptor` TODO in `mano_orchestrator.py`
   to match your VNFD
5. Restart `closed-loop-controller.service` -- no other file needs to change

## Tuning knobs (all env vars, see config.py for full list + defaults)

| Var | Purpose |
|---|---|
| `MIN_FORECAST_SAMPLES` | samples needed before forecaster is trusted (default 30) |
| `FORECAST_LOOKBACK_S` | history window fed to Holt's (default 3600s) |
| `HOLT_ALPHA` / `HOLT_BETA` | smoothing params -- raise for faster reaction, lower for stability |
| `EMBB_THRESHOLD_UP` / `URLLC_THRESHOLD_UP` / `MMTC_THRESHOLD_UP` | scale-up UE thresholds |
| `THRESHOLD_DOWN_MARGIN` | hysteresis gap (default 0.4 = scale-down at 60% of up-threshold) |
| `LATENCY_ALERT_MS` / `LOSS_ALERT_PCT` | QoS guardrail that bypasses forecast/cooldown |
| `ACTION_COOLDOWN_S` | anti-flap cooldown per slice (default 120s) |
| `MANO_BACKEND` | `kubectl` (default) or `osm` |
| `VERIFY_WAIT_S` / `VERIFY_IMPROVEMENT_MARGIN_PCT` | closed-loop verification tuning |

## Known simplifications (next steps, not done here)

- `verify_action()` runs synchronously per-slice inside the main loop --
  fine for 3 slices, but move to async/threaded if you add more slices,
  so one slice's 30s verify-wait doesn't delay others' decisions.
- `KubectlBackend` still uses `rollout restart` (recreate), not real
  `--replicas=N` scaling -- you already flagged this as blocked on SMF N4
  multi-UPF session routing verification (P2 in your git log). Swap it in
  `mano_orchestrator.py` once that's verified.
- Corrective retry in `verifier.py` is single-shot, no backoff/escalation.
- No persistence of `PolicyEngine`'s cooldown state across restarts (in
  memory only) -- move to metrics_store.py if the controller restarts often.
