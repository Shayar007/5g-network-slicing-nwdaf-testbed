"""
prometheus_exporter.py - pushes slice_profile.json metrics to the OSM
pushgateway so Grafana can visualize per-slice UE/UPF mapping and live stats.
"""
import json
import time
from pathlib import Path
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

PROFILE_PATH = Path("/home/comm/slice-test/slice_profile.json")
PUSHGATEWAY_ADDR = "localhost:9091"
JOB_NAME = "oai_slice_metrics"

SLICE_UPF_MAP = {
    "eMBB":  {"upf": "oai-upf",             "subnet": "12.1.1.0/24", "sst": "1"},
    "URLLC": {"upf": "oai-upf2",            "subnet": "12.2.1.0/24", "sst": "2"},
    "mMTC":  {"upf": "oai-upf-auto-auto01", "subnet": "12.3.1.0/24", "sst": "3"},
}

def push_metrics():
    if not PROFILE_PATH.exists():
        return
    try:
        data = json.loads(PROFILE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return

    registry = CollectorRegistry()
    flow_count_g = Gauge("slice_flow_count", "Flows classified per slice",
                          ["slice", "upf", "subnet", "sst"], registry=registry)
    throughput_g = Gauge("slice_throughput_kbps", "Throughput per slice (kbps)",
                          ["slice", "upf", "subnet", "sst"], registry=registry)
    gbr_flows_g = Gauge("slice_gbr_flows", "GBR flows per slice",
                         ["slice", "upf", "subnet", "sst"], registry=registry)
    slice_up_g = Gauge("slice_upf_up", "1 if slice has active flows this cycle",
                        ["slice", "upf", "subnet", "sst"], registry=registry)

    slices = data.get("slices", {})
    for slice_name, meta in SLICE_UPF_MAP.items():
        stats = slices.get(slice_name, {})
        labels = (slice_name, meta["upf"], meta["subnet"], meta["sst"])

        flow_count_g.labels(*labels).set(stats.get("flow_count", 0) or 0)
        throughput_g.labels(*labels).set(stats.get("total_throughput_kbps", 0) or 0)
        gbr_flows_g.labels(*labels).set(stats.get("gbr_flows", 0) or 0)
        slice_up_g.labels(*labels).set(1 if (stats.get("flow_count", 0) or 0) > 0 else 0)

    push_to_gateway(PUSHGATEWAY_ADDR, job=JOB_NAME, registry=registry)
    print(f"Pushed metrics to {PUSHGATEWAY_ADDR}")

if __name__ == "__main__":
    while True:
        push_metrics()
        time.sleep(10)
