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
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[mano:kubectl] {action_id} FAILED: {e.stderr[:300]}")
        return action_id


class OSMBackend(OrchestratorBackend):
    """
    Talks to OSM's NBI (North Bound Interface) REST API to scale a real
    NS (Network Service) instance instead of raw kubectl. This is the
    "proper" Layer-5 implementation once wired.

    TODO before use:
      1. Confirm OSM NBI is reachable (currently osm-nbi pod is Running in
         cluster but you'll likely need `kubectl port-forward -n osm
         svc/nbi 9999:9999` or an ingress, matching OSM_NBI_URL in config.py).
      2. Set OSM_USER / OSM_PASSWORD / OSM_PROJECT env vars.
      3. Set OSM_NS_EMBB / OSM_NS_URLLC / OSM_NS_MMTC to the actual NS
         instance names/IDs (`osm ns-list` on the OSM host, or via NBI
         GET /osm/nslcm/v1/ns_instances).
      4. Confirm the VNF's scaling-group-descriptor name in the VNFD (OSM
         scales via named scaling groups, not raw replica counts) and set
         it below where marked.
    """

    def __init__(self):
        self._token = None
        self._token_expiry = 0

    def _authenticate(self):
        import requests
        if self._token and time.time() < self._token_expiry:
            return self._token
        resp = requests.post(
            f"{CFG.OSM_NBI_URL}/admin/v1/tokens",
            json={
                "username": CFG.OSM_USER,
                "password": CFG.OSM_PASSWORD,
                "project_id": CFG.OSM_PROJECT,
            },
            headers={"Accept": "application/json"},  # OSM defaults to YAML otherwise
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["id"]
        self._token_expiry = time.time() + data.get("expires", 3600) - 30
        return self._token

    def scale_slice(self, slice_name: str, direction: str) -> str:
        import requests

        ns_id = CFG.OSM_NS_NAME.get(slice_name)
        if not ns_id:
            raise RuntimeError(
                f"OSM_NS_{slice_name.upper()} not configured -- set the env var "
                f"to the real NS instance id before using OSMBackend"
            )

        token = self._authenticate()
        action_id = f"osm-{slice_name}-{direction}-{uuid.uuid4().hex[:8]}"

        # TODO: replace "default-scaling-group" with the actual
        # scaling-group-descriptor name defined in your VNFD.
        payload = {
            "scaleType": "SCALE_VNF",
            "scaleVnfData": {
                "scaleVnfType": "SCALE_OUT" if direction == "up" else "SCALE_IN",
                "scaleByStepData": {
                    "scaling-group-descriptor": "default-scaling-group",
                },
            },
        }
        print(f"[mano:osm] {action_id}: scaling NS {ns_id} ({direction})")
        resp = requests.post(
            f"{CFG.OSM_NBI_URL}/nslcm/v1/ns_instances/{ns_id}/scale",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code >= 300:
            print(f"[mano:osm] {action_id} FAILED: {resp.status_code} {resp.text[:300]}")
        return action_id


def get_backend() -> OrchestratorBackend:
    if CFG.MANO_BACKEND == "osm":
        return OSMBackend()
    return KubectlBackend()
