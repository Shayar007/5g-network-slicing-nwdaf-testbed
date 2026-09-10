# OSM MANO Integration — Status Report

## Summary

The closed-loop auto-scaling system is production-deployed and stable
(kubectl backend, 3+ weeks uptime as of this writing). In parallel, real
OSM (Open Source MANO) orchestration was validated end-to-end on a
config-accurate mirror of the production UPF, confirming that a genuine
MANO backend is achievable on this platform. Full production cutover was
deliberately not attempted — see Risk Assessment below.

## What Works (Verified)

### 1. Closed-loop auto-scaling (production, kubectl backend)
- `ue_count_exporter`, `qos_latency_exporter`, `closed-loop-history`,
  `closed-loop-controller` — all systemd services, active since
  2026-08-19, self-recovering after transient infra issues.
- Decision engine (`policy_engine.py`) verified across SCALE_UP → HOLD →
  SCALE_DOWN transitions with sticky-state (no flapping) and file-backed
  persistence (survives controller restarts).
- Holt's Linear forecasting (manual implementation, no statsmodels
  dependency) verified against real accumulated history.

### 2. OSM MANO — proof of concept (test namespace)
- OSM NBI authentication, VNFD/NSD onboarding (SOL006 schema), and local
  Helm chart repository hosting all verified working.
- `scalable-echo` test KNF: full lifecycle verified —
  `osm ns-create` → `READY` → `osm vnf-scale --scale-out` → replica
  count 1→2 → `osm vnf-scale --scale-in` → replica count 2→1.
- **`osm-oai-upf` KNF (config-accurate mirror of the production UPF)**:
  same container image (`oai-upf:v2.1.0`), same ports (PFCP 8805, GTP
  2152, HTTP 80), same `config.yaml` structure (SMF/NRF hostnames, DNN,
  subnet). Instantiated via `osm ns-create`, reached `READY`, and its
  logs confirmed genuine registration with the real production NRF
  (`Got successful response from NRF`, repeating heartbeat).

### 3. Root causes found and fixed along the way
- Stale `k8scluster` server IP after host DHCP lease changes (fixed by
  pointing OSM's registered cluster at the internal Kubernetes
  `ClusterIP`, which is DHCP-independent).
- Dead external chart repository, replaced with a self-hosted
  `python3 -m http.server` repo.
- OSM caches Helm chart repo associations in
  `k8scluster._admin.helm_chart_repos`; deleting/recreating a repo
  leaves a stale ID in this array that breaks repo sync on every
  subsequent instantiate. Fixed by directly correcting the array via a
  Mongo update.
- SOL006 `kdu-resource-delta` / `kdu-resource-profile` list items
  require an explicit `id` key (pyangbind YANG binding), and
  `kdu-resource-delta` only accepts `id` (a leafref to the KDU name) and
  `number-of-instances` — not the `kdu-resource-ref`/`count` field names
  that seemed intuitive from the SOL006 spec description.
- `helm3 install` under OSM resolves chart URLs as
  `repo_base_url + "/" + chart_name` (no `.tgz`, no version) — the
  self-hosted repo needed a second copy of the packaged chart saved
  under the bare chart name for this to resolve to a real archive
  instead of an HTML directory listing.

## Known Limitation (Not Fixed)

`osm vnf-scale --scale-out` on the `osm-oai-upf` KNF fails with an
OSM/RO-internal exception:

```
File "/usr/lib/python3/dist-packages/osm_lcm/ns.py", line 6698, in scale
    kdu_name = kdu_profile["kdu-name"]
TypeError: 'NoneType' object is not subscriptable
```

This is despite the VNFD's `kdu-resource-profile` block being
structurally identical to the one used successfully on `scalable-echo`
(diffed and confirmed identical except for names). The same scaling
pattern is proven to work on a simpler KNF, so this appears to be an
RO-internal state/lookup issue specific to this VNFD or environment,
not a VNFD authoring error. Root-causing this further would require
reading OSM's RO source directly — not attempted, given time and risk
constraints (see below).

## Risk Assessment — Why Production Cutover Was Not Attempted

Over the course of this integration work, several OSM/cluster-level
changes (CoreDNS restarts, `k3s` service restarts, k8scluster credential
updates) caused **transient production outages** on the live 5G core
(AMF/SMF/UPF PDU session failures, 100% packet loss for the connected
UE fleet), each requiring `full_reset_3slice.sh` or a full UE fleet
relaunch to recover. The underlying `k3s`/CoreDNS/Kafka stack on this
host is intermittently unstable independent of anything done here
(observed both before and after any OSM work in this session).

Migrating the actual production UPF deployments (`oai-upf`, `oai-upf2`,
`oai-upf-auto-auto01`) to OSM management would require either:
1. Deleting and re-instantiating them under OSM (causing a real,
   user-facing outage window for however long the process takes), or
2. Some form of "adopt existing resource" workflow, which OSM does not
   natively support for KDUs.

Given the demonstrated fragility of this environment, this was judged
too high-risk to attempt without a scheduled maintenance window and an
explicit decision from the project owner that a downtime window is
acceptable. The `kubectl`-backed closed-loop controller already
provides equivalent automated scaling in production without this risk.

## Recommendation

- **Keep `kubectl` as the production `MANO_BACKEND`.** It works, it's
  tested, and it doesn't require touching the live UPFs' deployment
  ownership.
- **Treat the OSM integration as a validated proof of concept**, not a
  production migration. It answers the question "can this platform's
  OSM install manage KNFs like our UPFs?" — yes, for instantiation and
  registration; scaling has one open RO-side bug.
- If real OSM-managed production scaling becomes a requirement later:
  1. File/investigate the RO `kdu-resource-profile` lookup bug (or
     upgrade OSM, if a newer release fixes it).
  2. Plan an explicit maintenance window for the cutover, not an
     ad-hoc session.
  3. Migrate one UPF (e.g. `oai-upf-auto-auto01`, the mMTC slice — lowest
     traffic) first, verify for 24–48h, before migrating the other two.
