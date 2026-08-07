# P4: Multi-Node High Availability — Design Proposal

**Status:** Design only. Not implemented. This testbed runs on a single
machine (`comm-precision-3490`) with no second physical or virtual host
available, so none of this can be built or tested in this session.
Documenting the plan is the honest, achievable deliverable for this
priority given that constraint.

## Why single-node is a real limitation

Everything in this testbed — k3s, all UPFs, AMF/SMF/NRF, Prometheus,
Pushgateway, all five systemd sidecar services (`ue-count-exporter`,
`ml-predictor`, `predictive-prescaler`, `slice-testbed-watchdog`,
`alert-notifier`), and the reactive controller — runs on one host. This
was already visible operationally in this session: memory pressure and
image-pull issues on that single node caused cascading failures (the
Prometheus pod eviction/recovery incident, the pushgw-forward tmux
session dying silently for hours). In a real deployment, the host itself
is a single point of failure: if it goes down, the entire slice-serving
infrastructure goes down with it, with no failover.

## What "multi-node HA" would need to cover

### 1. Control-plane redundancy (Kubernetes level)
- Move from single-node k3s to a proper multi-node cluster: at minimum
  3 control-plane nodes (etcd quorum) + N worker nodes, or k3s's
  embedded-etcd HA mode with `--cluster-init` across 3+ server nodes.
- Worker nodes host the UPF/AMF/SMF/NRF pods; control-plane nodes stay
  dedicated to scheduling/API-server availability.

### 2. Core network function redundancy
- AMF, SMF, NRF currently run as single replicas. Each would need
  `replicas: 2+` with pod anti-affinity rules so replicas land on
  different nodes, plus a decision on active-active vs active-passive
  (5G core NFs are generally stateless at the HTTP/SBI layer and
  discoverable via NRF, so active-active is plausible — but this needs
  the same kind of session-affinity verification flagged in the P2
  design doc for UPF, applied here to AMF/SMF instead).
- NRF itself becomes a bootstrapping problem: if NRF is down, no other
  NF can (re)register. This typically needs either a highly-available
  NRF deployment or a documented bootstrap order.

### 3. Data-layer redundancy
- MySQL (subscriber data) currently has no replication — this session's
  own postmortem (`emptyDir` volume data loss on pod reschedule) is a
  direct illustration of this gap. Multi-node HA requires either a
  proper StatefulSet with a `PersistentVolumeClaim` backed by
  network-attached storage (not local-path), or a managed
  primary/replica MySQL setup.
- Prometheus: single instance today. HA options are either
  Prometheus's native `--web.enable-remote-write-receiver` +
  Thanos/Cortex for long-term multi-replica storage, or simpler
  dual-Prometheus-scraping-the-same-targets with Alertmanager dedup —
  the latter is much closer to this testbed's current scale and would
  be the pragmatic first step, not the former.

### 4. Sidecar services
- `ue-count-exporter.py`, `ml-predictor.py`, `predictive-prescaler.py`,
  `slice-testbed-watchdog`, `alert-notifier` are all systemd services
  tied to the one host today. In a multi-node design these become
  Kubernetes Deployments (not systemd units) so they can be
  rescheduled onto a healthy node automatically if their current node
  fails — this is a meaningful re-platforming, not a config change,
  since they currently assume `localhost` Prometheus/Pushgateway access
  in several places (see `PROMETHEUS_URL` defaults across the codebase).

### 5. RAN/UE simulation layer
- UERANSIM (gNB + UEs) is inherently tied to one host in this testbed's
  current design (it simulates radio, which doesn't have a
  meaningful "distributed" mode the way core NFs do). This part of the
  system would likely remain single-node even after HA work on the
  core — a real deployment replaces simulated UERANSIM with actual
  distributed RAN hardware, which is a different testbed altogether.

## Rollout order (if this were pursued)

1. **Data layer first** (#3) — this is the highest-value, lowest-risk
   starting point: MySQL replication + Prometheus dual-scrape can be
   added without touching NF logic at all, and directly fixes an
   already-demonstrated failure mode (data loss on reschedule).
2. **Sidecar re-platforming** (#4) — converting systemd services to
   Kubernetes Deployments is mechanical (no new distributed-systems
   design needed) and is a prerequisite for #1/#2 to matter, since a
   surviving-but-unreachable-from-old-node sidecar defeats the point of
   node HA.
3. **Control-plane HA** (#1) — needs the second/third physical or
   virtual host; blocked until that hardware exists.
4. **NF redundancy + session-affinity verification** (#2) — the
   highest-complexity item, and shares the same open question already
   flagged in the P2 (HPA scaling) design: does the SMF's session
   selection logic correctly handle multiple instances of a peer NF.
   This should be tackled together with the P2 SMF-N4 verification
   work, not separately, since they're the same underlying question
   applied to different NF pairs (SMF<->UPF for P2, AMF<->SMF/NRF here).

## What's out of scope even for a future pass

Physical infrastructure procurement, network topology changes beyond
what k3s multi-node requires, and RAN-side distribution are outside
what a software/testbed-level HA effort can address — those are
capital/infrastructure decisions, not engineering ones.

## Bottom line

Multi-node HA is not a single feature to implement — it's five
semi-independent redundancy problems (control-plane, NF, data, sidecar,
RAN) of increasing cost and decreasing tractability. The data-layer and
sidecar-replatforming items (#3, #4) are genuinely startable without new
hardware and would meaningfully reduce the single-point-of-failure risk
this session already observed firsthand. Control-plane and NF
redundancy (#1, #2) are blocked on hardware and on the same
session-affinity verification question already open from P2.
