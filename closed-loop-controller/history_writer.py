#!/usr/bin/env python3
"""
history_writer.py
Standalone, read-only service. Polls Prometheus (which ue_count_exporter.py
and qos_latency_exporter.py already push into) and appends clean samples to
the local SQLite history (metrics_store.py).

Does NOT touch UEs, UPFs, or any cluster state -- pure read from Prometheus,
write to local SQLite. Safe to run alongside everything else.

Run as its own systemd service (see README.md for a unit template),
independent of the existing exporters -- if this crashes, UE/QoS collection
is unaffected.
"""

import sys
import time

import requests

from config import CFG
import metrics_store as store

QUERIES = {
    "ue_count": 'slice_ue_count',
    "latency_ms": 'slice_avg_latency_ms',
    "loss_pct": 'slice_packet_loss_pct',
}


def _query_prometheus(promql):
    try:
        r = requests.get(
            f"{CFG.PROM_URL}/api/v1/query",
            params={"query": promql},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            print(f"Prometheus query failed: {promql} -> {data}", file=sys.stderr)
            return []
        return data["data"]["result"]
    except Exception as e:
        print(f"Prometheus query error ({promql}): {e}", file=sys.stderr)
        return []


def poll_once():
    now = time.time()
    rows = []
    for metric_name, promql in QUERIES.items():
        for result in _query_prometheus(promql):
            slice_name = result["metric"].get("slice")
            if slice_name:
                slice_name = slice_name.lower()  # normalize eMBB/URLLC/mMTC -> embb/urllc/mmtc
            if slice_name not in CFG.SLICES:
                continue
            try:
                value = float(result["value"][1])
            except (KeyError, ValueError, IndexError):
                continue
            rows.append((now, slice_name, metric_name, value))

    if rows:
        store.write_samples_bulk(rows)
        print(f"[history_writer] wrote {len(rows)} samples at {int(now)}")
    else:
        print("[history_writer] no samples this cycle (Prometheus empty/unreachable?)")


def main():
    store.init_db()
    print(f"history_writer: polling Prometheus every {CFG.HISTORY_POLL_INTERVAL_S}s, "
          f"writing to {CFG.DB_PATH}")
    last_prune = time.time()
    while True:
        poll_once()
        # Keep ~7 days of history by default; adjust to disk/needs.
        if time.time() - last_prune > 3600:
            store.prune_older_than(7 * 24 * 3600)
            last_prune = time.time()
        time.sleep(CFG.HISTORY_POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
