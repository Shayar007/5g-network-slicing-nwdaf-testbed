# Ghost-Duplicate UE Root Cause (Resolved 2026-08-04)

## Symptom
`nr-cli --dump` and downstream slice_ue_count occasionally showed 2 extra
entries beyond the true unique UE count (e.g. 53 vs 51, 47 vs 45).

## Root Cause
Two leftover reference config files, `/home/comm/ue-mmtc-WORKING.yaml` and
`/home/comm/ue-urllc-WORKING.yaml`, were provisioned with the SAME IMSI
(supi) as already-numbered configs:
  - ue-mmtc-WORKING.yaml  -> imsi-001010000000340 (duplicate of ue-mmtc-340.yaml)
  - ue-urllc-WORKING.yaml -> imsi-001010000000325 (duplicate of ue-urllc-325.yaml)

This was NOT a bug in ue_count_exporter.py, nr-cli, or any AMF/gNB state --
it was a testbed provisioning duplication (two live processes legitimately
registering under the same subscriber identity).

## Fix
Both WORKING.yaml UE processes were terminated (pkill -9), since the
properly-numbered equivalents already represent those same UEs.

## Verification
Before: 47 total nr-cli entries, 2 IMSIs duplicated (x2 each) -> 45 unique
After:  45 total nr-cli entries, 0 duplicates, all IMSIs unique
Prometheus/exporter continued operating correctly throughout (no crash).
