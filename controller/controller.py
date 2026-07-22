#!/usr/bin/env python3
"""
NWDAF Slice-Specific Auto-Scaling Controller v3
- Per-slice UE threshold (eMBB/URLLC/mMTC configurable via env)
- Independent UPF deploy per slice
- FIX (2026-07-08): v2 queried MongoDB ("testing" db, amf/smf collections),
  but this deployment's AMF/SMF never write registration state there
  (they use MySQL for subscriber profiles only; runtime registration
  state is in-memory). Replaced with a parser over `kubectl logs
  deploy/oai-amf`, which prints a full 5GMM-state table every ~20s.
  Slice is inferred from IMSI, since the AMF table doesn't show S-NSSAI
  directly. IMSI->slice ranges match the current provisioning scheme:
    eMBB : 100,105,109,200,201,202, 300-324
    URLLC: 225, 325-339
    mMTC : 240, 340-349
  Update IMSI_SLICE_RANGES below if the provisioning scheme changes.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import textwrap
import time

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [nwdaf-ctrl] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nwdaf-ctrl")

POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL",   "15"))
EMBB_THRESHOLD  = int(os.getenv("EMBB_THRESHOLD",  "10"))
URLLC_THRESHOLD = int(os.getenv("URLLC_THRESHOLD", "3"))
MMTC_THRESHOLD  = int(os.getenv("MMTC_THRESHOLD",  "20"))
NAMESPACE       = os.getenv("NAMESPACE", "oai")
AMF_LOG_TAIL    = int(os.getenv("AMF_LOG_TAIL", "500"))

SLICES = {
    "embb":  {"sst": 1, "sd": "FFFFFF", "threshold": EMBB_THRESHOLD,
               "dnn": "oai",       "subnet": "12.1.1.0/24"},
    "urllc": {"sst": 2, "sd": "000002",        "threshold": URLLC_THRESHOLD,
               "dnn": "oai-urllc", "subnet": "12.2.1.0/24"},
    "mmtc":  {"sst": 3, "sd": "FFFFFF", "threshold": MMTC_THRESHOLD,
               "dnn": "oai-mmtc",  "subnet": "12.3.1.0/24"},
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

REG_LINE_RE = re.compile(r'\|\s*\d+\|\s*(5GMM-[A-Za-z\-]+)\|\s*(\d+)\s*\|')


def imsi_to_slice(imsi: str):
    try:
        n = int(imsi[-3:])
    except ValueError:
        return None
    if n in IMSI_SLICE_SINGLES:
        return IMSI_SLICE_SINGLES[n]
    for lo, hi, slice_name in IMSI_SLICE_RANGES:
        if lo <= n <= hi:
            return slice_name
    return None


def run_kubectl(args, timeout=30):
    cmd = ["/usr/local/bin/kubectl"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
    return r.stdout, r.stderr, r.returncode


def query_slice_counts():
    try:
        stdout, stderr, rc = run_kubectl([
            "logs", "-n", NAMESPACE, "deployment/oai-amf",
            f"--tail={AMF_LOG_TAIL}"
        ], timeout=15)
        if rc != 0:
            log.warning(f"kubectl logs failed (rc={rc}): {stderr.strip()[:200]}")
            return None

        latest_state = {}
        for line in stdout.splitlines():
            m = REG_LINE_RE.search(line)
            if m:
                state, imsi = m.group(1), m.group(2)
                latest_state[imsi] = state

        if not latest_state:
            log.warning("no registration rows found in AMF logs")
            return None

        counts = {"embb": 0, "urllc": 0, "mmtc": 0}
        for imsi, state in latest_state.items():
            if state != "5GMM-REGISTERED":
                continue
            slice_name = imsi_to_slice(imsi)
            if slice_name:
                counts[slice_name] += 1
            else:
                log.warning(f"IMSI {imsi} REGISTERED but has no known slice mapping")

        result = {}
        for slice_name, cfg in SLICES.items():
            key = f"sst{cfg['sst']}_sd{cfg['sd']}"
            result[key] = counts.get(slice_name, 0)
        return result
    except subprocess.TimeoutExpired:
        log.warning("kubectl logs query timed out (15s)")
        return None
    except Exception as e:
        log.warning(f"query_slice_counts error: {e}")
        return None


# Some slices already have a UPF deployed under a non-standard name
# from earlier manual/other-tool provisioning. Map those here so the
# controller recognizes them instead of deploying a duplicate.
UPF_NAME_OVERRIDES = {
    "embb": "oai-upf",
    "urllc": "oai-upf2",
    "mmtc": "oai-upf-auto-auto01",
}

def upf_deployment_name(slice_name):
    return UPF_NAME_OVERRIDES.get(slice_name, f"oai-upf-auto-{slice_name}")


def upf_exists(slice_name):
    _, _, rc = run_kubectl([
        "get", "deployment", upf_deployment_name(slice_name),
        "-n", NAMESPACE
    ])
    return rc == 0


def make_upf_yaml(slice_name, cfg):
    dep_name = upf_deployment_name(slice_name)
    return textwrap.dedent(f"""\
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: {dep_name}
      namespace: {NAMESPACE}
      labels:
        app: {dep_name}
        managed-by: nwdaf-controller
        slice: {slice_name}
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: {dep_name}
      template:
        metadata:
          labels:
            app: {dep_name}
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
              name: {dep_name}-config
    ---
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: {dep_name}-config
      namespace: {NAMESPACE}
    data:
      config.yaml: |
        log_level:
          general: info
        register_nf:
          general: yes
        http_version: 2
        snssais:
          - &slice
            sst: {cfg['sst']}
            sd: '{cfg['sd']}'
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
                - dnn: {cfg['dnn']}
        dnns:
          - dnn: "{cfg['dnn']}"
            pdu_session_type: "IPV4"
            ipv4_subnet: "{cfg['subnet']}"
    """)


def deploy_upf(slice_name, cfg):
    yaml_content = make_upf_yaml(slice_name, cfg)
    try:
        r = subprocess.run(
            ["/usr/local/bin/kubectl", "apply", "-f", "-"],
            input=yaml_content, capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            log.info(f"[{slice_name.upper()}] UPF deployed: {upf_deployment_name(slice_name)}")
            log.info(f"[{slice_name.upper()}] {r.stdout.strip()}")
            return True
        else:
            log.error(f"[{slice_name.upper()}] deploy FAILED: {r.stderr.strip()[:300]}")
            return False
    except Exception as e:
        log.error(f"[{slice_name.upper()}] deploy exception: {e}")
        return False


def patch_smf(slice_name):
    log.info(f"[{slice_name.upper()}] Waiting 60s for UPF NRF registration...")
    time.sleep(60)
    _, _, rc = run_kubectl(
        ["rollout", "restart", "deployment/oai-smf", "-n", NAMESPACE]
    )
    if rc == 0:
        log.info(f"[{slice_name.upper()}] SMF restarted — will pick up new UPF")
    else:
        log.warning(f"[{slice_name.upper()}] SMF restart failed (rc={rc})")


async def main():
    log.info("=" * 55)
    log.info("  NWDAF Slice Auto-Scaling Controller v3 (AMF-log source)")
    log.info("=" * 55)
    log.info(f"  Poll     : {POLL_INTERVAL}s")
    log.info(f"  eMBB thr : {EMBB_THRESHOLD} UEs")
    log.info(f"  URLLC thr: {URLLC_THRESHOLD} UEs")
    log.info(f"  mMTC thr : {MMTC_THRESHOLD} UEs")
    log.info("=" * 55)

    deployed = set()
    loop = asyncio.get_event_loop()

    for slice_name in SLICES:
        if upf_exists(slice_name):
            log.info(f"[{slice_name.upper()}] UPF already exists — skipping")
            deployed.add(slice_name)

    while True:
        await asyncio.sleep(POLL_INTERVAL)

        counts = query_slice_counts()
        if counts is None:
            log.warning("AMF log source unreachable — skipping poll")
            continue

        for slice_name, cfg in SLICES.items():
            key      = f"sst{cfg['sst']}_sd{cfg['sd']}"
            ue_count = counts.get(key, 0)
            thr      = cfg["threshold"]
            status   = "EXISTS" if slice_name in deployed else "NONE"

            log.info(
                f"[{slice_name.upper():5s}] "
                f"UEs={ue_count:3d}  thr={thr:3d}  UPF={status}"
            )

            if ue_count >= thr and slice_name not in deployed:
                log.info(
                    f"[{slice_name.upper()}] THRESHOLD CROSSED "
                    f"({ue_count}>={thr}) — deploying UPF..."
                )
                if deploy_upf(slice_name, cfg):
                    deployed.add(slice_name)
                    loop.run_in_executor(None, patch_smf, slice_name)

if __name__ == "__main__":
    asyncio.run(main())
