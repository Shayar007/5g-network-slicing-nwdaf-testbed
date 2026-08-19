# Production Readiness Roadmap
### Slice Testbed → Real Operator Data (Airtel / Jio / Vi)

**Honest framing:** this testbed demonstrates a *methodology* (RAN-truth
UE counting, reactive+predictive auto-scaling, operational hardening).
Making it genuinely production-ready for a real telecom operator is a
multi-month, multi-person engineering + security + compliance effort.
This document tracks what's done, what's realistic to build ahead of
time, and what fundamentally cannot be finished without real
infrastructure/data/access that only an operator can provide.

---

## ✅ Done

### 1. PII / IMSI protection layer (`pii_utils.py`) -- COMPLETE (2026-08-18)
Real subscriber identities (IMSI/SUPI) must never reach logs, Prometheus
labels, or git history in plaintext. This module provides:
- `anonymize_imsi()` -- one-way salted SHA-256 hash, consistent per
  subscriber (needed for aggregation), not reversible without the salt.
- `mask_imsi_for_display()` -- human-readable masked form for logs
  (`************340`), matching common telecom masking convention.

**Wiring status, audited 2026-08-18:**
- `ue_count_exporter.py` -- wired; defensively masks any IMSI-shaped
  string that might reach an error-path print.
- `controller.py` -- audited, does NOT touch raw IMSI in live code.
  Its docstring still references IMSI_SLICE_RANGES from an older,
  pre-Prometheus, AMF-log-based version of the script; that logic was
  removed when the controller was rewritten to query Prometheus's
  `slice_ue_count` directly (v3). The comment is stale documentation,
  not live behavior -- no code change was needed.
- `predictive_prescaler.py` -- audited, contains zero IMSI references
  anywhere in the file. Operates purely on aggregated Prometheus
  metrics.

**Conclusion: P0 is complete.** `ue_count_exporter.py` is the only
script that ever handles a raw IMSI, and it is already wired.

### 2. Offline historical-data ingestion (`data_source.py`)
Real operator data will most realistically arrive as a **historical
export** (CSV/Parquet of PM counters or KPI aggregates), not a live API
feed -- operators do not typically grant live production access to
external research testbeds. `data_source.py` lets `ml_predictor.py`'s
forecasting logic run unchanged against either:
- `DATA_SOURCE=prometheus` (current live-testbed behavior), or
- `DATA_SOURCE=csv` (reads `timestamp,slice,value` rows from a file).

Wired into `ml_predictor.py` and verified working in both modes.

---

## ⏳ Realistic to build ahead of time (before any real-data pilot)

| Item | Est. effort | Why it matters |
|---|---|---|
| TLS/mTLS between all NFs | 2–4 days | Currently zero transport security anywhere |
| RBAC + non-root service accounts (currently everything runs as root/sudo) | 2–3 days | Basic cluster hygiene, unrelated to real data specifically |
| Secrets management (DB passwords, salts currently in plaintext env/config) | 1–2 days | `oai_db` password, IMSI salt, etc. are all plaintext today |
| Replace threshold-existence UPF scaling with real HPA-style replica scaling | 1–2 weeks | Current logic only checks "does 1 UPF exist," not real load-based replica count |
| Upgrade forecaster to a proper time-series model (multiple features, seasonality) | 1–2 weeks | Single-feature linear regression / Holt's won't capture real daily/weekly traffic patterns |
| Multi-node HA for core network + monitoring | Weeks (needs hardware) | Single-host is a hard single point of failure today |
| Characterize predictive-trigger reproducibility across load patterns | 2-3 days | One isolated trigger demonstrated and confirmed; general reliability across arbitrary patterns not yet measured |

## ❌ Cannot be done without operator involvement

- **Actual data access.** No engineering effort on this testbed
  substitutes for an operator granting a data-sharing agreement, NDA,
  and a real export or sandboxed API.
- **Compliance certification** (DPDP Act / telecom-specific regulatory
  requirements for handling real subscriber data) -- requires legal/
  compliance review, not just code changes.
- **Validation that the UERANSIM-observed AMF-staleness bug (Section IV
  of the paper) even applies to real vendor equipment.** UERANSIM is a
  research simulator; production gNBs from commercial vendors implement
  NGAP-level radio-link-failure detection more completely, and may not
  exhibit the same stale-registration failure mode this testbed found
  and fixed. This should be explicitly caveated wherever the finding is
  presented, and re-validated against real equipment if/when available.
- **Offsite/remote backup of scripts and configuration.** Version
  control is local to the host (`~/slice-testbed-repo`, git); no
  remote/offsite repository was configured, since the cluster has no
  outbound internet access.
- **Elimination of the single point of failure.** Everything (core
  network + monitoring/control plane) runs on one physical host;
  redundancy would require additional hardware.

---

## Bottom line

The methodology (RAN-truth counting, reactive+predictive scaling,
operational hardening patterns) is the transferable output of this
work. The code as it stands is a research testbed, not a
production-ready system. PII protection (P0) is now fully wired and
verified; the remaining gaps are either bounded engineering work
(estimated above) or genuinely blocked on external factors (hardware,
operator access, compliance) that this session cannot resolve on its
own.
