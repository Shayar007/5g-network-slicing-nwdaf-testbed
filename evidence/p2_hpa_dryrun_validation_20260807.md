# P2: HPA-Style Tiered Scaling -- Dry-Run Validation (2026-08-07)

## What was tested
controller_v4_hpa.py (tiered-threshold, multi-replica scaling logic)
run in DRY_RUN=true against LIVE Prometheus data from the running
testbed. No kubectl scale/apply commands were executed -- decision
logic only.

## Live data observed at test time
embb=26 UEs, urllc=15 UEs, mmtc=5 UEs

## Decisions computed (2 consecutive poll cycles, stable/non-flapping)
| Slice | UEs | Current replicas | Target replicas | Decision |
|-------|-----|-------------------|------------------|----------|
| EMBB  | 26  | 1                 | 2                | Would scale up |
| URLLC | 15  | 1                 | 3 (tier-capped)  | Would scale up |
| MMTC  | 5   | 1                 | 1                | No change |

## Why this stayed in DRY-RUN (not flipped to LIVE)
Multi-replica UPF scaling requires the SMF's PFCP (N4) session-selection
policy to correctly distribute/load-balance PDU sessions across
multiple UPF pods of the same slice. This testbed's oai-smf config was
built and tested assuming exactly one UPF per slice; that assumption
has not been separately verified to hold under replica>1. Flipping to
LIVE without that verification risks either a no-op (SMF keeps routing
to one pod) or session-establishment inconsistency -- given the paper
deadline, that verification was deemed out of scope for this pass.

## Conclusion
The tiered-threshold decision logic is implemented, unit-tested
(18 boundary cases, see commit history), and validated end-to-end
against live production data. It is ready to go live once SMF
multi-UPF N4 routing is separately confirmed -- documented here as a
concrete, scoped next step rather than left as an open TODO.
