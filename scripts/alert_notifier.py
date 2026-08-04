#!/usr/bin/env python3
"""
alert_notifier.py
Polls Alertmanager for firing alerts and broadcasts them visibly to all
logged-in terminal users via `wall`, plus writes to a highly visible
CURRENT_ALERTS.txt file. This is a low-infrastructure fallback since no
SMTP/webhook receiver is configured for the slice_testbed_alert_rules
group -- it does NOT replace a proper notification channel, but ensures
an operator physically at the machine cannot miss an active incident.

Only reacts to alerts from the "slice_testbed_alert_rules" group -- does
not touch or duplicate the existing OSM/VDU alerting behavior.
"""

import subprocess
import time
import urllib.request
import json
from datetime import datetime

ALERTMANAGER_URL = "http://localhost:9093"
POLL_INTERVAL = 30
ALERTS_FILE = "/home/comm/CURRENT_ALERTS.txt"

_previously_firing = set()


def get_firing_alerts():
    try:
        with urllib.request.urlopen(f"{ALERTMANAGER_URL}/api/v2/alerts", timeout=10) as r:
            alerts = json.loads(r.read().decode())
    except Exception as e:
        print(f"could not reach alertmanager: {e}")
        return []

    firing = []
    for a in alerts:
        if a.get("status", {}).get("state") != "active":
            continue
        name = a.get("labels", {}).get("alertname", "")
        if not name.startswith("slice_testbed_"):
            continue
        summary = a.get("annotations", {}).get("summary", name)
        firing.append((name, summary))
    return firing


def write_alerts_file(firing):
    with open(ALERTS_FILE, "w") as f:
        if not firing:
            f.write(f"[{datetime.now()}] No active slice-testbed alerts.\n")
        else:
            f.write(f"[{datetime.now()}] ACTIVE ALERTS:\n")
            for name, summary in firing:
                f.write(f"  - {name}: {summary}\n")


def broadcast_new_alerts(firing):
    global _previously_firing
    current = {name for name, _ in firing}
    new_alerts = current - _previously_firing
    for name, summary in firing:
        if name in new_alerts:
            msg = f"SLICE TESTBED ALERT: {summary}"
            try:
                subprocess.run(["wall", msg], timeout=5)
            except Exception as e:
                print(f"wall broadcast failed: {e}")
    _previously_firing = current


def main():
    print(f"alert_notifier: polling Alertmanager every {POLL_INTERVAL}s")
    while True:
        firing = get_firing_alerts()
        write_alerts_file(firing)
        broadcast_new_alerts(firing)
        if firing:
            print(f"active alerts: {[n for n, _ in firing]}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
