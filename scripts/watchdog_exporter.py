#!/usr/bin/env python3
"""
watchdog_exporter.py (v2)
Generic watchdog for the slice testbed. Pushes two kinds of 1/0 gauges to
the Prometheus Pushgateway:
  - slice_testbed_service_up{service=...}: systemd service health
  - slice_testbed_upf_up{slice=...}: whether each slice's UPF Deployment
    has at least 1 ready replica (checked via kubectl directly, since
    kube-state-metrics is NOT installed in this cluster -- confirmed
    2026-08-03, `kube_deployment_status_replicas_available` returns no data).
"""

import subprocess
import time

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

PUSHGATEWAY_ADDR = "localhost:9091"
JOB_NAME = "slice_testbed_watchdog"
POLL_INTERVAL = 20
NAMESPACE = "oai"

SERVICES = [
    "ue-count-exporter",
    "ml-predictor",
    "predictive-prescaler",
]

UPF_DEPLOYMENTS = {
    "embb": "oai-upf",
    "urllc": "oai-upf2",
    "mmtc": "oai-upf-auto-auto01",
}


def is_service_active(service_name):
    r = subprocess.run(
        ["systemctl", "is-active", service_name],
        capture_output=True, text=True, timeout=5,
    )
    return 1 if r.stdout.strip() == "active" else 0


def is_upf_ready(deployment_name):
    r = subprocess.run(
        ["kubectl", "get", "deployment", deployment_name, "-n", NAMESPACE,
         "-o", "jsonpath={.status.readyReplicas}"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return 0
    try:
        return 1 if int(r.stdout.strip() or "0") >= 1 else 0
    except ValueError:
        return 0


def push_status():
    registry = CollectorRegistry()
    g_svc = Gauge("slice_testbed_service_up", "1 if systemd service is active, else 0",
                  ["service"], registry=registry)
    g_upf = Gauge("slice_testbed_upf_up", "1 if slice's UPF deployment has >=1 ready replica, else 0",
                  ["slice"], registry=registry)

    for svc in SERVICES:
        try:
            status = is_service_active(svc)
        except Exception:
            status = 0
        g_svc.labels(service=svc).set(status)

    for slice_name, dep_name in UPF_DEPLOYMENTS.items():
        try:
            status = is_upf_ready(dep_name)
        except Exception:
            status = 0
        g_upf.labels(slice=slice_name).set(status)

    push_to_gateway(PUSHGATEWAY_ADDR, job=JOB_NAME, registry=registry)


def main():
    print(f"watchdog_exporter v2: polling every {POLL_INTERVAL}s")
    print(f"  services: {SERVICES}")
    print(f"  upf deployments: {UPF_DEPLOYMENTS}")
    while True:
        try:
            push_status()
            print("pushed status OK")
        except Exception as e:
            print(f"push failed: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
