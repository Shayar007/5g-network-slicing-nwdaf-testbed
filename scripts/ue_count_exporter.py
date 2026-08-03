#!/usr/bin/env python3
"""
ue_count_exporter.py (v2 -- RAN-truth based)
Standalone, read-only exporter that counts UEs per slice.

WHY THIS VERSION EXISTS:
The original approach parsed `kubectl logs deployment/oai-amf` and counted
IMSIs marked 5GMM-REGISTERED. That was fixed once (only trust the latest
table dump, not the whole tail window) but a deeper issue remained: the
AMF's registration table itself does not get updated when a UE is killed
ungracefully (crash, pkill, radio loss) -- there is no NGAP UE Context
Release / NAS Deregistration signal in that case, so the AMF keeps
believing the UE is registered indefinitely (until a long periodic-
registration timer eventually expires).

`nr-cli --dump` on the other hand queries UERANSIM's own live gNB/UE
registry directly -- it was confirmed to correctly drop IMSIs the moment
their process died (no AMF round-trip needed). So this version uses that
as the UE-liveness source of truth instead of AMF logs.

Does NOT touch or interact with the live auto-scaling controller in any
way -- pure read-only observation, safe to run alongside it.
"""

import os
import re
import subprocess
import time

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

NR_CLI_PATH = os.getenv("NR_CLI_PATH", "/home/comm/UERANSIM/build/nr-cli")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
PUSHGATEWAY_ADDR = os.getenv("PUSHGATEWAY_ADDR", "localhost:9091")
JOB_NAME = "oai_slice_ue_count"

THRESHOLDS = {
    "embb": int(os.getenv("EMBB_THRESHOLD", "10")),
    "urllc": int(os.getenv("URLLC_THRESHOLD", "3")),
    "mmtc": int(os.getenv("MMTC_THRESHOLD", "5")),
}

UPF_NAME_OVERRIDES = {
    "embb": "oai-upf",
    "urllc": "oai-upf2",
    "mmtc": "oai-upf-auto-auto01",
}

IMSI_SLICE_SINGLES = {100: "embb", 105: "embb", 109: "embb",
                       200: "embb", 201: "embb", 202: "embb",
                       225: "urllc",
                       240: "mmtc"}
IMSI_SLICE_RANGES = [
    (300, 324, "embb"),
    (325, 339, "urllc"),
    (340, 349, "mmtc"),
]

# nr-cli --dump prints lines like: imsi-001010000000347
IMSI_LINE_RE = re.compile(r'^imsi-(\d+)$')


def imsi_to_slice(imsi_full: str):
    try:
        n = int(imsi_full[-3:])
    except ValueError:
        return None
    if n in IMSI_SLICE_SINGLES:
        return IMSI_SLICE_SINGLES[n]
    for lo, hi, slice_name in IMSI_SLICE_RANGES:
        if lo <= n <= hi:
            return slice_name
    return None


def query_ue_counts():
    try:
        r = subprocess.run(
            [NR_CLI_PATH, "--dump"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            print(f"nr-cli --dump failed (rc={r.returncode}): {r.stderr[:200]}")
            return None

        counts = {"embb": 0, "urllc": 0, "mmtc": 0}
        for line in r.stdout.splitlines():
            line = line.strip()
            m = IMSI_LINE_RE.match(line)
            if not m:
                continue  # skips gNB name lines etc.
            imsi_full = m.group(1)
            slice_name = imsi_to_slice(imsi_full)
            if slice_name:
                counts[slice_name] += 1
        return counts
    except subprocess.TimeoutExpired:
        print("nr-cli --dump timed out")
        return None
    except Exception as e:
        print(f"query_ue_counts error: {e}")
        return None


def push_metrics(counts):
    registry = CollectorRegistry()
    g_count = Gauge("slice_ue_count", "Registered UEs per slice",
                     ["slice", "sst", "upf"], registry=registry)
    g_thresh = Gauge("slice_ue_threshold", "Auto-scaling UE threshold per slice",
                      ["slice", "sst"], registry=registry)

    sst_map = {"embb": 1, "urllc": 2, "mmtc": 3}
    for slice_name, count in counts.items():
        sst = sst_map[slice_name]
        upf = UPF_NAME_OVERRIDES[slice_name]
        g_count.labels(slice=slice_name, sst=str(sst), upf=upf).set(count)
        g_thresh.labels(slice=slice_name, sst=str(sst)).set(THRESHOLDS[slice_name])

    push_to_gateway(PUSHGATEWAY_ADDR, job=JOB_NAME, registry=registry)


def main():
    print(f"ue_count_exporter (v2, nr-cli based): polling every {POLL_INTERVAL}s, "
          f"pushing to {PUSHGATEWAY_ADDR}")
    while True:
        counts = query_ue_counts()
        if counts is not None:
            push_metrics(counts)
            print(f"pushed: {counts}")
        else:
            print("skipping push (no data)")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
