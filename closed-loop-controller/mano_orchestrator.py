#!/usr/bin/env python3
"""
mano_orchestrator.py (Layer 5 -- MANO / Orchestration)

Two backends behind one interface:

  KubectlBackend -- what the testbed does manually today
    (`kubectl rollout restart` / `kubectl scale`), wrapped so the rest of
    the pipeline doesn't care which backend is active. This is the safe
    default and needs zero extra setup.

  OSMBackend -- talks to the REAL OSM instance already running in the
    `osm` namespace (lcm/ro/nbi/mon pods were confirmed Running). This is
    a STUB: OSM NBI auth + NS scaling calls are sketched with TODOs
    because the actual NS instance IDs / project / credentials are
    site-specific and weren't available here. Fill in the TODOs and flip
    MANO_BACKEND=osm to switch over -- no other module needs to change.

Both backends expose the same scale_slice(slice_name, direction) contract
and return an action_id string used by verifier.py.
"""

import subprocess
import time
import uuid
from abc import ABC, abstractmethod

from config import CFG


class OrchestratorBackend(ABC):
    @abstractmethod
    def scale_slice(self, slice_name: str, direction: str) -> str:
        """direction: 'up' or 'down'. Returns an opaque action_id for logging."""
        ...


class KubectlBackend(OrchestratorBackend):
    """
    Wraps today's manual pattern (`kubectl rollout restart deployment/<upf>`)
    behind the same interface as OSM, so swapping backends later is a
    one-line config change, not a rewrite.

    NOTE: rollout restart is a blunt instrument (recreates the pod, doesn't
    truly add capacity). Replace with `kubectl scale --replicas=N` once the
    SMF's N4 multi-UPF session routing is verified for real replica-based
    scaling (this was flagged as deferred in your P2 controller work).
    """

    def scale_slice(self, slice_name: str, direction: str) -> str:
        deployment = CFG.UPF_NAME[slice_name]
        action_id = f"kubectl-{slice_name}-{direction}-{uuid.uuid4().hex[:8]}"
        cmd = ["kubectl", "rollout", "restart",
               f"deployment/{deployment}", "-n", CFG.K8S_NAMESPACE]
        print(f"[mano:kubectl] {action_id}: {' '.join(cmd)}")
        import os
        if os.getenv("DEMO_DRY_RUN") == "1":
            print(f"[mano:kubectl] {action_id}: DRY-RUN -- decision made, UPF not touched (demo mode)")
            return action_id
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[mano:kubectl] {action_id} FAILED: {e.stderr[:300]}")
        return action_id


class OSMBackend(OrchestratorBackend):
    """
    OSM MANO actuation: a scale decision becomes an OSM scale operation (via the osm client / NBI)
    on the slice's scaling group; replica convergence is then verified on the cluster.
    Slices with no OSM NS configured fall back to KubectlBackend (which honours DEMO_DRY_RUN).
    """

    def _ready(self, dep, ns):
        r = subprocess.run(["kubectl", "get", "deploy", dep, "-n", ns, "-o", "jsonpath={.status.readyReplicas}"],
                           capture_output=True, text=True, timeout=20)
        return int(r.stdout.strip() or 0)

    def scale_slice(self, slice_name: str, direction: str) -> str:
        import os
        ns_name = CFG.OSM_NS_NAME.get(slice_name)
        if not ns_name:
            return KubectlBackend().scale_slice(slice_name, direction)
        action_id = f"osm-{slice_name}-{direction}-{uuid.uuid4().hex[:8]}"
        dep = os.getenv("OSM_KDU_DEPLOY", "scalable-echo")
        kns = os.getenv(f"OSM_KDU_NS_{slice_name.upper()}") or os.getenv("OSM_KDU_NS", "")
        vnf = os.getenv("OSM_VNF_INDEX", "echo_vnf")
        group = os.getenv("OSM_SCALING_GROUP", "echo-scale")
        before = self._ready(dep, kns)
        if direction == "down" and before <= 1:
            print(f"[mano:osm] {action_id}: scale-in skipped, already at minimum ({before} replica)", flush=True)
            return action_id
        flag = "--scale-out" if direction == "up" else "--scale-in"
        target = before + 1 if direction == "up" else before - 1
        cmd = ["osm", "vnf-scale", ns_name, vnf, "--scaling-group", group, flag]
        print(f"[mano:osm] {action_id}: {' '.join(cmd)} (replicas {before} -> {target})", flush=True)
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            print(f"[mano:osm] {action_id} FAILED: {(r.stdout + r.stderr)[:300]}", flush=True)
            return action_id
        while time.time() - t0 < 150:
            if self._ready(dep, kns) == target:
                print(f"[mano:osm] {action_id}: OSM scale op converged, {dep} ready replicas {before} -> {target} in {time.time() - t0:.1f}s", flush=True)
                return action_id
            time.sleep(3)
        print(f"[mano:osm] {action_id}: replicas did not reach {target} within 150s", flush=True)
        return action_id


def get_backend() -> OrchestratorBackend:
    if CFG.MANO_BACKEND == "osm":
        return OSMBackend()
    return KubectlBackend()
