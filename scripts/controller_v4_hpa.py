#!/usr/bin/env python3
"""
NWDAF Slice-Specific Auto-Scaling Controller v4 (HPA-style, P2 production-readiness item)

WHAT CHANGED FROM v3
---------------------
v3 made a binary decision per slice: "UPF deployed or not" (0 or 1 replica).
Real production slicing needs N replicas based on load tiers -- a single
UPF instance becomes a throughput/session-count bottleneck well before
"does a UPF exist at all" stops being the right question.

This version replaces the single THRESHOLD per slice with TIERED
thresholds mapping UE-count ranges to a target replica count, and
replaces the exists-or-deploy logic with a read-current / scale-to-target
loop (the actual Kubernetes primitive for this is `kubectl scale`, which
is idempotent and safe to call every poll cycle even when no change is
needed).

IMPORTANT CAVEAT -- WHY THIS DEFAULTS TO DRY-RUN
--------------------------------------------------
Scaling a Kubernetes Deployment to N replicas is the easy part. Whether
those N replicas actually share load correctly depends on the SMF's PFCP
(N4) session-selection policy -- which UPF instance a given PDU session
gets pinned to at establishment time. This testbed's oai-smf config was
built and tested assuming exactly one UPF per slice; it has NOT been
verified to correctly distribute or load-balance N4 sessions across
multiple UPF replicas of the same slice. Blindly scaling to replicas=3
could:
  - have zero effect (SMF keeps routing everything to whichever UPF pod
    it discovered first via NRF), or
  - cause session-establishment inconsistency if the SMF's internal UPF
    selection isn't idempotent under replica churn.
Until that is specifically tested (out of scope for this pass, given the
paper deadline), this controller runs in DRY_RUN=true by default: it
computes and logs the scaling DECISION every cycle (which is genuinely
useful -- it's exactly the logic a real HPA-style controller needs and
can be validated against live Prometheus data), but does not call
`kubectl scale`. Flip DRY_RUN=false only after SMF multi-UPF N4 routing
has been separately validated.

Everything else (Prometheus-based UE counting, deployment naming,
UPF/ConfigMap manifest, SMF-notify-after-first-deploy) is unchanged from
v3 -- see that file's docstring for that history.

Usage:
  DRY_RUN=true python3 controller_v4_hpa.py    # decision-logic only, no kubectl scale
  DRY_RUN=false python3 controller_v4_hpa.py   # LIVE -- only after SMF N4 verification
"""

import asyncio
import json
import logging
import os
import subprocess
import textwrap
import time
import urllib.parse
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [nwdaf-ctrl-v4] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nwdaf-ctrl-v4")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
NAMESPACE = os.getenv("NAMESPACE", "oai")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus.osm.svc.cluster.local:9090")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
MAX_REPLICAS = int(os.getenv("MAX_REPLICAS", "3"))  # hard safety cap regardless of tiers

# TIERED_THRESHOLDS: list of (ue_count_at_or_above, target_replicas), MUST be
# sorted ascending by threshold. The highest tier whose threshold the
# current UE count meets or exceeds wins. A count below the first tier's
# threshold means 0 replicas (no UPF needed yet) UNLESS the first tier's
# threshold is 0 (meaning "always keep at least 1 replica").
#
# Defaults below are illustrative, scaled to this testbed's existing
# single-slice thresholds (EMBB_THRESHOLD=10, URLLC_THRESHOLD=3,
# MMTC_THRESHOLD=20 in v3) with two additional higher tiers each, so the
# decision logic has something meaningful to compute against real data
# without requiring an unrealistically large UE count to see tier 2/3 fire.
TIERED_THRESHOLDS = {
    "embb":  [(10, 1), (20, 2), (35, 3)],
    "urllc": [(3, 1), (8, 2), (15, 3)],
    "mmtc":  [(5, 1), (15, 2), (30, 3)],
}

SLICE_CFG = {
    "embb":  {"sst": 1, "sd": "FFFFFF", "dnn": "oai",      "subnet": "12.1.1.0/24"},
    "urllc": {"sst": 2, "sd": "000002", "dnn": "oai-urllc", "subnet": "12.2.1.0/24"},
    "mmtc":  {"sst": 3, "sd": "000003", "dnn": "oai-mmtc",  "subnet": "12.3.1.0/24"},
}

UPF_NAME_OVERRIDES = {
    "embb": "oai-upf",
    "urllc": "oai-upf2",
    "mmtc": "oai-upf-auto-auto01",
}


def run_kubectl(args, timeout=30):
    cmd = ["/usr/local/bin/kubectl"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
    return r.stdout, r.stderr, r.returncode


def upf_deployment_name(slice_name):
    return UPF_NAME_OVERRIDES.get(slice_name, f"oai-upf-auto-{slice_name}")


def query_slice_counts():
    """Identical approach to v3: live UE counts from Prometheus's slice_ue_count."""
    try:
        url = f"{PROMETHEUS_URL}/api/v1/query?" + urllib.parse.urlencode(
            {"query": "slice_ue_count"}
        )
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode())

        if payload.get("status") != "success":
            log.warning(f"Prometheus query returned non-success status: {payload.get('status')}")
            return None

        counts = {"embb": 0, "urllc": 0, "mmtc": 0}
        found_any = False
        for series in payload.get("data", {}).get("result", []):
            slice_label = series.get("metric", {}).get("slice")
            if slice_label not in counts:
                continue
            try:
                counts[slice_label] = int(float(series["value"][1]))
                found_any = True
            except (KeyError, IndexError, ValueError):
                continue

        if not found_any:
            log.warning("no slice_ue_count series returned from Prometheus "
                        "(is ue_count_exporter.py running?)")
            return None
        return counts
    except Exception as e:
        log.warning(f"Prometheus query_slice_counts error: {e}")
        return None


def current_replica_count(slice_name):
    """
    Returns the deployment's current READY replica count, or 0 if the
    deployment doesn't exist yet. Using readyReplicas (not spec.replicas)
    so a deployment that's scaling up but not yet healthy doesn't get
    double-counted as "already at target".
    """
    dep_name = upf_deployment_name(slice_name)
    out, _, rc = run_kubectl([
        "get", "deployment", dep_name, "-n", NAMESPACE,
        "-o", "jsonpath={.status.readyReplicas}"
    ])
    if rc != 0:
        return 0
    out = out.strip()
    if not out:
        return 0
    try:
        return int(out)
    except ValueError:
        return 0


def target_replicas_for(slice_name, ue_count):
    """
    Walk TIERED_THRESHOLDS for this slice, return the replica count for
    the highest tier whose threshold is met, or 0 if below the lowest tier.
    Result is capped at MAX_REPLICAS regardless of tier configuration.
    """
    tiers = TIERED_THRESHOLDS[slice_name]
    target = 0
    for threshold, replicas in tiers:
        if ue_count >= threshold:
            target = replicas
    return min(target, MAX_REPLICAS)


def make_upf_yaml(slice_name, cfg, replicas):
    dep_name = upf_deployment_name(slice_name)
    return textwrap.dedent(f"""\
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: {dep_name}
      namespace: {NAMESPACE}
      labels:
        app: {dep_name}
        managed-by: nwdaf-controller-v4
        slice: {slice_name}
    spec:
      replicas: {replicas}
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


def deploy_upf_at_replicas(slice_name, cfg, replicas):
    """First-time creation, at the given replica count."""
    yaml_content = make_upf_yaml(slice_name, cfg, replicas)
    try:
        r = subprocess.run(
            ["/usr/local/bin/kubectl", "apply", "-f", "-"],
            input=yaml_content, capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            log.info(f"[{slice_name.upper()}] UPF deployed at {replicas} replica(s): "
                      f"{upf_deployment_name(slice_name)}")
            return True
        else:
            log.error(f"[{slice_name.upper()}] deploy FAILED: {r.stderr.strip()[:300]}")
            return False
    except Exception as e:
        log.error(f"[{slice_name.upper()}] deploy exception: {e}")
        return False


def scale_upf(slice_name, target_replicas):
    """Scale an existing deployment. kubectl scale is idempotent."""
    dep_name = upf_deployment_name(slice_name)
    out, err, rc = run_kubectl([
        "scale", "deployment", dep_name, "-n", NAMESPACE,
        f"--replicas={target_replicas}"
    ])
    if rc == 0:
        log.info(f"[{slice_name.upper()}] scaled {dep_name} -> {target_replicas} replicas")
        return True
    else:
        log.error(f"[{slice_name.upper()}] scale FAILED: {err.strip()[:300]}")
        return False


def patch_smf(slice_name):
    log.info(f"[{slice_name.upper()}] Waiting 60s for UPF NRF registration...")
    time.sleep(60)
    _, _, rc = run_kubectl(["rollout", "restart", "deployment/oai-smf", "-n", NAMESPACE])
    if rc == 0:
        log.info(f"[{slice_name.upper()}] SMF restarted -- will pick up new UPF")
    else:
        log.warning(f"[{slice_name.upper()}] SMF restart failed (rc={rc})")


async def main():
    mode = "DRY-RUN (decision-logic only, no kubectl scale)" if DRY_RUN else "LIVE (will scale deployments)"
    log.info("=" * 65)
    log.info("  NWDAF Slice Auto-Scaling Controller v4 (HPA-style, tiered)")
    log.info(f"  Mode: {mode}")
    log.info("=" * 65)
    for slice_name, tiers in TIERED_THRESHOLDS.items():
        tier_str = ", ".join(f"{ue}+ UEs->{r} replicas" for ue, r in tiers)
        log.info(f"  {slice_name.upper():6s} tiers: {tier_str}")
    log.info(f"  Max replicas cap: {MAX_REPLICAS}")
    log.info("=" * 65)
    if not DRY_RUN:
        log.warning("Running LIVE. This WILL call kubectl scale. Confirm SMF N4 "
                     "multi-UPF routing has been validated before using this mode.")

    loop = asyncio.get_event_loop()

    while True:
        await asyncio.sleep(POLL_INTERVAL)

        counts = query_slice_counts()
        if counts is None:
            log.warning("Prometheus source unreachable -- skipping poll")
            continue

        for slice_name, cfg in SLICE_CFG.items():
            ue_count = counts.get(slice_name, 0)
            current = current_replica_count(slice_name)
            target = target_replicas_for(slice_name, ue_count)

            log.info(
                f"[{slice_name.upper():5s}] UEs={ue_count:3d}  "
                f"current_replicas={current}  target_replicas={target}"
            )

            if target == current:
                continue

            if DRY_RUN:
                log.info(f"[{slice_name.upper()}] [DRY-RUN] Would change "
                          f"{current} -> {target} replicas. No cluster change made.")
                continue

            if current == 0 and target > 0:
                if deploy_upf_at_replicas(slice_name, cfg, target):
                    loop.run_in_executor(None, patch_smf, slice_name)
            elif target > 0:
                scale_upf(slice_name, target)
            elif target == 0 and current > 0:
                log.info(f"[{slice_name.upper()}] target=0 but current={current} -- "
                          f"NOT scaling down to 0 automatically (would drop active "
                          f"sessions on this slice; scale-to-zero needs a drain step "
                          f"not implemented here). Leaving as-is.")


if __name__ == "__main__":
    asyncio.run(main())
