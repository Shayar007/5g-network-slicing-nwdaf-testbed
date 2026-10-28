#!/usr/bin/env python3
"""Creates an extra UPF through OSM when a slice's UE count reaches a per-UPF capacity.
Default is DRY RUN. Real mode: UPF_AUTO_DRY_RUN=0. Creation only; no automatic deletion
(deleting a registered UPF crashes SMF v2.0.0). SMF does not select the new UPF yet."""
import os, sys, time, json, subprocess, urllib.request, urllib.parse
PROM = os.getenv("PROM_URL", "http://localhost:9090")
DRY = os.getenv("UPF_AUTO_DRY_RUN", "1") == "1"
POLL = int(os.getenv("UPF_AUTO_POLL_S", "30"))
SUSTAIN = int(os.getenv("UPF_AUTO_SUSTAIN", "2"))
VIM = os.getenv("OSM_VIM", "d99f1603-cd89-4efd-8177-aab4c134bb59")
NSD = os.getenv("OSM_UPF_NSD", "osm_oai_upf_ns")
LOG = os.path.expanduser(os.getenv("UPF_AUTO_LOG", "~/upf_autoscale_events.log"))
CAP = {"embb": int(os.getenv("EMBB_UPF_CAP", "50")),
       "urllc": int(os.getenv("URLLC_UPF_CAP", "15")),
       "mmtc": int(os.getenv("MMTC_UPF_CAP", "40"))}
# overflow UPF identity per slice: distinct S-NSSAI, DNN and subnet so nothing clashes with live slices
OVF = {"embb": (5, "000005", "oai-ovf-embb", "12.10.1.0/24"),
       "urllc": (6, "000006", "oai-ovf-urllc", "12.11.1.0/24"),
       "mmtc": (7, "000007", "oai-ovf-mmtc", "12.12.1.0/24")}

def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    with open(LOG, "a") as f: f.write(line + "\n")

def sh(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr)

def prom_ue(s):
    try:
        q = urllib.parse.urlencode({"query": f'slice_ue_count{{slice="{s}"}}'})
        d = json.load(urllib.request.urlopen(f"{PROM}/api/v1/query?{q}", timeout=10))
        r = d["data"]["result"]
        return float(r[0]["value"][1]) if r else None
    except Exception as e:
        log(f"prometheus error: {e}"); return None

def ns_state(name):
    for line in sh(["osm", "ns-list"]).splitlines():
        c = [x.strip() for x in line.split("|")]
        if len(c) > 5 and c[1] == name: return c[4]
    return None

def smf_restarts():
    out = sh(["kubectl", "get", "pods", "-n", "oai", "--no-headers"])
    for l in out.splitlines():
        if l.startswith("oai-smf"): return l.split()[3]
    return "?"

def create(s):
    sst, sd, dnn, subnet = OVF[s]
    name, ns = f"upf-auto-{s}", f"upf-auto-{s}"
    st = ns_state(name)
    if st:
        log(f"[{s}] NS {name} already exists (state {st}), skipping"); return
    if DRY:
        log(f"[{s}] DRY RUN: would create NS {name} from {NSD} (sst {sst}, sd {sd}, dnn {dnn}, subnet {subnet}) in namespace {ns}"); return
    before = smf_restarts(); t0 = time.time()
    log(f"[{s}] creating NS {name}; SMF restarts before: {before}")
    sh(["kubectl", "create", "namespace", ns])
    cfg = ("{additionalParamsForVnf: [{member-vnf-index: upf_vnf, additionalParamsForKdu: [{kdu_name: upfapp, "
           f"k8s-namespace: {ns}, additionalParams: {{smfHost: oai-smf.oai.svc.cluster.local, "
           f"nrfHost: oai-nrf.oai.svc.cluster.local, sst: {sst}, sd: \"{sd}\", dnn: {dnn}, ipv4Subnet: \"{subnet}\"}}}}]}}]}}")
    log(sh(["osm", "ns-create", "--ns_name", name, "--nsd_name", NSD, "--vim_account", VIM, "--config", cfg], 120).strip())
    state = None
    while time.time() - t0 < 240:
        state = ns_state(name)
        if state in ("READY", "BROKEN"): break
        time.sleep(5)
    log(f"[{s}] NS {name} state {state} after {time.time() - t0:.1f}s")
    if state != "READY":
        log(f"[{s}] check: osm ns-op-list {name}"); return
    time.sleep(25)
    log(f"[{s}] pods in {ns}: " + " | ".join(sh(["kubectl", "get", "pods", "-n", ns, "--no-headers"]).split("\n")))
    log(f"[{s}] SMF restarts after: {smf_restarts()} (before: {before})")

def main():
    a = sys.argv[1:]
    log(f"start dry_run={DRY} caps={CAP}")
    if "--force" in a:
        create(a[a.index("--force") + 1]); return
    once = "--once" in a
    need = 1 if once else SUSTAIN
    streak = {s: 0 for s in CAP}
    while True:
        for s in CAP:
            u = prom_ue(s)
            if u is None: continue
            streak[s] = streak[s] + 1 if u >= CAP[s] else 0
            log(f"[{s}] ue={u:.0f} cap={CAP[s]} streak={streak[s]}/{need}")
            if streak[s] >= need: create(s)
        if once: break
        time.sleep(POLL)

if __name__ == "__main__": main()
