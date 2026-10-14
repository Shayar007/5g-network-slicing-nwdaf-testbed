#!/usr/bin/env python3
"""Live slice exporter: UE-tunnel byte counters + conntrack + k8s UPF status -> Pushgateway (same metrics/labels as prometheus_exporter.py)."""
import json, re, subprocess, time
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

PUSHGATEWAY_ADDR = "localhost:9091"
JOB_NAME = "oai_slice_metrics"
INTERVAL = 10
# gbr: from the subscription 5QI in the DB (eMBB 5, URLLC 2 = GBR, mMTC 6)
SLICES = {
    "eMBB":  {"upf": "oai-upf",             "subnet": "12.1.1.0/24", "sst": "1", "prefix": "12.1.", "gbr": False},
    "URLLC": {"upf": "oai-upf2",            "subnet": "12.2.1.0/24", "sst": "2", "prefix": "12.2.", "gbr": True},
    "mMTC":  {"upf": "oai-upf-auto-auto01", "subnet": "12.3.1.0/24", "sst": "3", "prefix": "12.3.", "gbr": False},
}
last = {}

def slice_of(ip):
    for name, m in SLICES.items():
        if ip.startswith(m["prefix"]):
            return name

def tun_ips():
    out = subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True).stdout
    for i in json.loads(out or "[]"):
        if i.get("ifname", "").startswith("uesimtun"):
            for a in i.get("addr_info", []):
                yield i["ifname"], a["local"]

def throughput_kbps():
    now, tot, seen = time.time(), {s: 0.0 for s in SLICES}, set()
    for ifn, ip in tun_ips():
        s = slice_of(ip)
        if not s:
            continue
        try:
            b = sum(int(open(f"/sys/class/net/{ifn}/statistics/{d}").read()) for d in ("rx_bytes", "tx_bytes"))
        except OSError:
            continue
        seen.add(ifn)
        if ifn in last:
            t0, b0 = last[ifn]
            if now > t0 and b >= b0:
                tot[s] += (b - b0) * 8 / 1000 / (now - t0)
        last[ifn] = (now, b)
    for k in list(last):
        if k not in seen:
            del last[k]
    return tot

def flows():
    cnt = {s: 0 for s in SLICES}
    r = subprocess.run(["conntrack", "-L"], capture_output=True, text=True)
    if r.returncode != 0 and not r.stdout:
        print("conntrack unavailable:", r.stderr.strip()[:150], flush=True)
        return None
    for line in r.stdout.splitlines():
        m = re.search(r"\bsrc=(\d+\.\d+)\.", line)
        if m:
            s = slice_of(m.group(1) + ".")
            if s:
                cnt[s] += 1
    return cnt

def upf_up(dep):
    r = subprocess.run(["kubectl", "get", "deploy", dep, "-n", "oai", "-o", "jsonpath={.status.readyReplicas}"], capture_output=True, text=True)
    return 1 if r.stdout.strip().isdigit() and int(r.stdout.strip()) > 0 else 0

def cycle():
    tp, fl = throughput_kbps(), flows()
    reg, L = CollectorRegistry(), ["slice", "upf", "subnet", "sst"]
    g_fc = Gauge("slice_flow_count", "Flows per slice (conntrack, UE subnet source)", L, registry=reg)
    g_tp = Gauge("slice_throughput_kbps", "Throughput per slice (kbps, UE tunnel counters)", L, registry=reg)
    g_gb = Gauge("slice_gbr_flows", "GBR flows per slice", L, registry=reg)
    g_up = Gauge("slice_upf_up", "1 if the slice UPF deployment has a ready replica", L, registry=reg)
    for s, m in SLICES.items():
        lab = (s, m["upf"], m["subnet"], m["sst"])
        n = float("nan") if fl is None else fl[s]
        g_fc.labels(*lab).set(n)
        g_tp.labels(*lab).set(round(tp[s], 2))
        g_gb.labels(*lab).set(n if m["gbr"] else 0)
        g_up.labels(*lab).set(upf_up(m["upf"]))
    push_to_gateway(PUSHGATEWAY_ADDR, job=JOB_NAME, registry=reg)
    cm = subprocess.run(["kubectl", "create", "configmap", "slice-throughput-live", "-n", "oai", "--dry-run=client", "-o", "yaml"]
                        + [f"--from-literal={s.lower()}={round(v, 1)}" for s, v in tp.items()], capture_output=True, text=True)
    subprocess.run(["kubectl", "apply", "-f", "-"], input=cm.stdout, text=True, capture_output=True, timeout=15)
    print("pushed", {s: round(v, 1) for s, v in tp.items()}, fl, flush=True)

if __name__ == "__main__":
    while True:
        try:
            cycle()
        except Exception as e:
            print("cycle error:", e, flush=True)
        time.sleep(INTERVAL)
