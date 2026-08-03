# Slice Testbed -- Source of Truth

**RULE: Never edit controller.py or exporter scripts by pasting directly into
a live terminal / ConfigMap. Always edit here, then deploy via deploy.sh.**

## Layout
- scripts/          -- working copies of all Python components
- systemd/          -- systemd unit files for host-side services
- k8s/               -- kubectl-exported snapshots of live cluster state
- deploy.sh          -- the ONLY approved way to push controller.py changes

## Components
- ue_count_exporter.py  : pushes slice_ue_count to Prometheus (source: nr-cli --dump)
- ml_predictor.py       : advisory-only traffic predictor
- predictive_prescaler.py : DRY_RUN by default; only flip to LIVE deliberately
- controller.py         : reactive autoscaling controller (runs in-cluster,
                           reads slice_ue_count from Prometheus)

## Known incident (2026-07-31)
A raw-paste of an incomplete base64 blob into `kubectl create configmap`
silently produced an EMPTY controller.py, which passed py_compile (empty
file = valid syntax) and got deployed, disabling the controller with no
crash/error. Root cause of the fix: ALWAYS verify wc -l + grep for known
function names BEFORE applying to the cluster. See deploy.sh for the
enforced version of this check.
