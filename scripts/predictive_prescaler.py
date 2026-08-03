#!/usr/bin/env python3
"""
predictive_prescaler.py
LIVE predictive auto-scaling: reads ML-predicted UE counts (pushed by
ml_predictor.py) and, when a slice is predicted to cross its threshold
soon but hasn't reactively crossed it yet, proactively deploys that
slice's UPF -- ahead of the reactive nwdaf-slice-controller.

SAFETY DESIGN (read before running in LIVE mode):
- This is a SEPARATE process/deployment from nwdaf-slice-controller.
  It does not modify, restart, or depend on that controller's code.
  If this script misbehaves, the reactive controller is unaffected
  and continues working exactly as already proven.
- Kubernetes Deployments are declarative/idempotent: applying the same
  UPF manifest from both this script and the reactive controller
  converges to the same single-replica state -- it does not cause the
  duplicate-replica issue seen with the earlier elastic-scaling
  experiment (that issue was caused by independent replica-count
  tracking logic, not by declarative kubectl apply itself).
- DRY_RUN=true (default) logs the action it WOULD take without calling
  kubectl. Set DRY_RUN=false only after confirming dry-run logs look
  correct for at least one full prediction cycle per slice.
- CONFIRM_CYCLES: requires the prediction to exceed threshold for this
  many consecutive polls before acting, to avoid triggering on a
  single noisy/transient prediction spike.

Usage:
  DRY_RUN=true python3 predictive_prescaler.py     # test first
  DRY_RUN=false python3 predictive_prescaler.py    # then go live
"""

import os
import subprocess
import time
import logging
import tempfile

import requests

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [predictive-prescaler] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
NAMESPACE = os.getenv("NAMESPACE", "oai")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "20"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
CONFIRM_CYCLES = int(os.getenv("CONFIRM_CYCLES", "2"))

THRESHOLDS = {"embb": 10, "urllc": 3, "mmtc": 5}

# Manifests follow the exact provisioning scheme already in the paper
# (Table II): eMBB SST1/oai/12.1.1.0-24, URLLC SST2/oai-urllc/12.2.1.0-24,
# mMTC SST3/oai-mmtc/12.3.1.0-24. Same pattern proven working during
# manual oai-upf recovery earlier in this deployment.
SLICE_CONFIG = {
    "embb": {
        "deployment_name": "oai-upf",
        "configmap_name": "oai-upf-config",
        "sst": 1, "sd": "FFFFFF", "dnn": "oai", "subnet": "12.1.1.0/24",
    },
    "urllc": {
        "deployment_name": "oai-upf2",
        "configmap_name": "oai-upf2-config",
        "sst": 2, "sd": "000002", "dnn": "oai-urllc", "subnet": "12.2.1.0/24",
    },
    "mmtc": {
        "deployment_name": "oai-upf-auto-auto01",
        "configmap_name": "oai-upf-auto-auto01-config",
        "sst": 3, "sd": "FFFFFF", "dnn": "oai-mmtc", "subnet": "12.3.1.0/24",
    },
}

MANIFEST_TEMPLATE = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {deployment_name}
  namespace: {namespace}
  labels:
    app: {deployment_name}
    managed-by: predictive-prescaler
    slice: {slice_name}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {deployment_name}
  template:
    metadata:
      labels:
        app: {deployment_name}
        slice: {slice_name}
    spec:
      initContainers:
      - name: init-sysctl
        image: busybox
        command: ["sh", "-c", "sysctl -w net.ipv4.ip_forward=1"]
        securityContext:
          privileged: true
      containers:
      - name: upf
        image: docker.io/oaisoftwarealliance/oai-upf:v2.1.0
        imagePullPolicy: IfNotPresent
        securityContext:
          privileged: true
        volumeMounts:
        - name: config
          mountPath: /openair-upf/etc
      volumes:
      - name: config
        configMap:
          name: {configmap_name}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {configmap_name}
  namespace: {namespace}
data:
  config.yaml: |
    log_level:
      general: info
    register_nf:
      general: yes
    http_version: 2
    snssais:
      - &slice
        sst: {sst}
        sd: '{sd}'
    nfs:
      smf:
        host: oai-smf
        sbi:
          port: 80
          api_version: v1
          interface_name: eth0
        n4:
          interface_name: eth0
          port: 8805
      nrf:
        host: oai-nrf
        sbi:
          port: 80
          api_version: v1
          interface_name: eth0
    upf:
      support_features:
        enable_bpf_datapath: no
        enable_snat: yes
      remote_n6_gw: 127.0.0.1
      upf_info:
        sNssaiUpfInfoList:
        - sNssai: *slice
          dnnUpfInfoList:
            - dnn: {dnn}
    dnns:
      - dnn: "{dnn}"
        pdu_session_type: "IPV4"
        ipv4_subnet: "{subnet}"
"""

_confirm_state = {slice_name: 0 for slice_name in SLICE_CONFIG}


def query_instant(metric):
    try:
        r = requests.get(f"{PROMETHEUS_URL}/api/v1/query",
                          params={"query": metric}, timeout=10)
        r.raise_for_status()
        results = {}
        for series in r.json().get("data", {}).get("result", []):
            slice_label = series["metric"].get("slice", "unknown")
            value = float(series["value"][1])
            results[slice_label] = value
        return results
    except Exception as e:
        log.warning(f"Prometheus query failed for {metric}: {e}")
        return {}


def upf_exists(deployment_name):
    r = subprocess.run(
        ["kubectl", "get", "deployment", deployment_name, "-n", NAMESPACE],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def deploy_upf(slice_name):
    cfg = SLICE_CONFIG[slice_name]
    manifest = MANIFEST_TEMPLATE.format(namespace=NAMESPACE, slice_name=slice_name, **cfg)

    if DRY_RUN:
        log.info(f"[DRY-RUN] Would apply manifest for {slice_name} "
                 f"(deployment={cfg['deployment_name']}). No cluster change made.")
        return

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(manifest)
        manifest_path = f.name

    log.info(f"[LIVE] Applying manifest for {slice_name} "
             f"(deployment={cfg['deployment_name']})")
    r = subprocess.run(["kubectl", "apply", "-f", manifest_path],
                        capture_output=True, text=True)
    if r.returncode != 0:
        log.error(f"kubectl apply failed for {slice_name}: {r.stderr.strip()}")
    else:
        log.info(f"[LIVE] {slice_name} UPF applied successfully: {r.stdout.strip()}")


def main():
    mode = "DRY-RUN (no cluster changes)" if DRY_RUN else "LIVE (will deploy UPFs)"
    log.info("=" * 60)
    log.info("  Predictive Pre-Scaler")
    log.info(f"  Mode: {mode}")
    log.info(f"  Confirm cycles required: {CONFIRM_CYCLES}")
    log.info("=" * 60)
    if not DRY_RUN:
        log.warning("Running in LIVE mode. This WILL modify the cluster "
                     "when a predictive trigger fires.")

    while True:
        predicted = query_instant("slice_ue_count_predicted")
        current = query_instant("slice_ue_count")

        for slice_name, threshold in THRESHOLDS.items():
            pred_val = predicted.get(slice_name)
            cur_val = current.get(slice_name, 0)
            cfg = SLICE_CONFIG[slice_name]
            already_deployed = upf_exists(cfg["deployment_name"])

            if pred_val is None:
                log.info(f"[{slice_name.upper():6s}] no prediction available yet")
                _confirm_state[slice_name] = 0
                continue

            will_cross_soon = pred_val >= threshold
            already_reactive_triggered = cur_val >= threshold

            if already_deployed:
                log.info(f"[{slice_name.upper():6s}] UPF already deployed -- "
                          f"predictive check skipped")
                _confirm_state[slice_name] = 0
                continue

            if already_reactive_triggered:
                # Reactive controller will handle this on its own next poll;
                # predictive pre-scaling adds no value here.
                log.info(f"[{slice_name.upper():6s}] current={cur_val:.0f} already "
                          f">= threshold -- reactive controller will handle this")
                _confirm_state[slice_name] = 0
                continue

            if will_cross_soon:
                _confirm_state[slice_name] += 1
                log.info(f"[{slice_name.upper():6s}] predicted={pred_val:.1f} "
                          f">= threshold={threshold}, current={cur_val:.0f} "
                          f"(confirm {_confirm_state[slice_name]}/{CONFIRM_CYCLES})")
                if _confirm_state[slice_name] >= CONFIRM_CYCLES:
                    log.info(f"[{slice_name.upper():6s}] PREDICTIVE TRIGGER "
                              f"CONFIRMED -- pre-scaling ahead of reactive threshold")
                    deploy_upf(slice_name)
                    _confirm_state[slice_name] = 0
            else:
                _confirm_state[slice_name] = 0
                log.info(f"[{slice_name.upper():6s}] predicted={pred_val:.1f} "
                          f"< threshold={threshold}, current={cur_val:.0f}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
