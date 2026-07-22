"""
slice_scheduler.py

Purpose
-------
Runs on a schedule (every N seconds). Each cycle it:
  1. Pulls a batch of new flow/session records (from Airtel data source -
     Kafka topic, DB table, CSV drop folder, whatever you wire in).
  2. Classifies each record into eMBB / URLLC / mMTC using the 3GPP TS
     23.501 Table 5.7.4-1 standardized 5QI -> QoS characteristics mapping
     as the primary rule, with a heuristic fallback for records that don't
     carry a 5QI.
  3. Aggregates the classified records into a per-slice traffic profile
     (volume, average latency budget, GBR share, priority mix).
  4. Emits that profile as JSON, which is what your NSSF/MANO side reads
     to decide whether to (re)provision UPF instances per slice.

This file has two independently useful halves:
  - CLASSIFICATION (Sections 1-2): pure functions, no side effects, fully
    unit-testable on their own.
  - SCHEDULER + INTEGRATION (Sections 3-5): wiring that runs it periodically
    and pushes results into your k3s/OAI environment.

Install:
    pip install schedule kubernetes --break-system-packages
    # 'schedule' runs the periodic job. 'kubernetes' is only needed if you
    # use the optional scale_upf_for_slice() integration in Section 5.
"""

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Optional

import schedule

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("slice_scheduler")


# ---------------------------------------------------------------------------
# Section 1: 5QI -> slice mapping (from TS 23.501 Table 5.7.4-1)
# ---------------------------------------------------------------------------
# Slice assignment logic, derived directly from the standardized table:
#
#   URLLC  -> Delay-critical GBR 5QIs (82-90) and any GBR/Delay-critical 5QI
#             with Packet Delay Budget <= 30 ms (discrete automation, V2X,
#             motion tracking, electricity distribution protection).
#   eMBB   -> Non-GBR / GBR 5QIs serving video, voice, browsing, streaming
#             with PDB roughly 100-300 ms and higher throughput expectations.
#   mMTC   -> 3GPP doesn't define an explicit "mMTC 5QI" (mMTC traffic is
#             mostly small, infrequent, delay-tolerant, non-GBR). We treat
#             the lowest-priority, highest-PDB, non-GBR 5QIs (8, 9, 10) as
#             the mMTC-shaped proxy class, matching how IoT background/
#             telemetry traffic is typically provisioned in a slice.
#
# Each entry: 5QI -> (slice, resource_type, priority_level, pdb_ms)
FIVE_QI_TABLE = {
    # --- Conversational / real-time (mostly eMBB, 3 is often used for URLLC demos) ---
    1: ("eMBB", "GBR", 20, 100),
    2: ("eMBB", "GBR", 40, 150),
    3: ("URLLC", "GBR", 30, 50),
    4: ("eMBB", "GBR", 50, 300),
    # --- Non-GBR video / signalling / browsing -> eMBB ---
    5: ("eMBB", "Non-GBR", 10, 100),
    6: ("eMBB", "Non-GBR", 60, 300),
    7: ("eMBB", "Non-GBR", 70, 100),
    8: ("mMTC", "Non-GBR", 80, 300),
    9: ("mMTC", "Non-GBR", 90, 300),
    10: ("mMTC", "Non-GBR", 90, 1100),
    # --- Low-latency eMBB / V2X non-GBR ---
    79: ("URLLC", "Non-GBR", 65, 50),
    80: ("URLLC", "Non-GBR", 68, 10),
    # --- Delay-critical GBR: discrete automation, V2X, motion tracking, XR -> URLLC ---
    82: ("URLLC", "Delay-critical GBR", 19, 10),
    83: ("URLLC", "Delay-critical GBR", 22, 10),
    84: ("URLLC", "Delay-critical GBR", 24, 30),
    85: ("URLLC", "Delay-critical GBR", 21, 5),
    86: ("URLLC", "Delay-critical GBR", 18, 5),
    87: ("URLLC", "Delay-critical GBR", 25, 5),
    88: ("URLLC", "Delay-critical GBR", 25, 10),
    89: ("eMBB", "Delay-critical GBR", 25, 15),   # split/cloud rendering: bursty + latency-aware
    90: ("eMBB", "Delay-critical GBR", 25, 20),
    # --- Mission critical (not URLLC by definition, but latency-bound -> URLLC bucket for slicing purposes) ---
    65: ("URLLC", "GBR", 7, 75),
    66: ("eMBB", "GBR", 20, 100),
    67: ("URLLC", "GBR", 15, 100),
    69: ("URLLC", "Non-GBR", 5, 60),
    70: ("eMBB", "Non-GBR", 55, 200),
}

DEFAULT_SLICE = "eMBB"  # fallback bucket when nothing else matches


def classify_by_5qi(five_qi: Optional[int]) -> Optional[str]:
    """Primary path: direct standardized 5QI lookup. Returns None if unknown."""
    if five_qi is None:
        return None
    entry = FIVE_QI_TABLE.get(int(five_qi))
    return entry[0] if entry else None


def classify_by_heuristics(record: dict) -> str:
    """
    Fallback path for records with a non-standard / dynamically-assigned 5QI,
    or no 5QI at all (common in raw CDR-style big data exports).
    Uses packet size, latency, and throughput as proxies for the same
    3GPP characteristics (PDB, resource type) the 5QI table encodes.
    """
    latency_ms = record.get("latency_ms")
    throughput_kbps = record.get("throughput_kbps", 0) or 0
    packet_size_bytes = record.get("packet_size_bytes", 0) or 0
    session_interval_s = record.get("session_interval_s")  # time between sessions, IoT signal

    # Sparse, small, infrequent -> mMTC
    if session_interval_s is not None and session_interval_s > 300 and packet_size_bytes < 200:
        return "mMTC"

    # Tight latency bound -> URLLC
    if latency_ms is not None and latency_ms <= 30:
        return "URLLC"

    # High throughput, latency-tolerant -> eMBB
    if throughput_kbps >= 2000:
        return "eMBB"

    return DEFAULT_SLICE


# Well-known ports as a proxy for traffic type when there's no 5QI or
# latency at all - e.g. raw NetFlow / packet-capture exports. This is a
# coarse approximation, not a real QoS signal: DNS/NTP/SNMP-style control
# traffic behaves like mMTC (small, sporadic), bulk web/video ports behave
# like eMBB (large, latency-tolerant). There's no reliable NetFlow proxy
# for true URLLC without an actual latency measurement, so this path never
# emits URLLC - it only separates eMBB-like from mMTC-like traffic.
_MMTC_LIKE_PORTS = {53, 123, 161, 162, 67, 68}   # DNS, NTP, SNMP, DHCP
_EMBB_LIKE_PORTS = {80, 443, 8080, 8443}         # HTTP(S), web/video/streaming


def classify_by_network_flow(record: dict) -> Optional[str]:
    """
    Proxy classifier for raw NetFlow-style rows (Src/Dst IP, Port, Packets,
    Octets, Protocol) - no 5QI, no latency. Returns None if nothing matches,
    so the caller can fall through to the generic heuristic/default.
    """
    protocol = str(record.get("protocol") or "").upper()
    dst_port = record.get("dst_port")
    packets = record.get("packets") or 0
    octets = record.get("octets") or 0
    avg_pkt_size = (octets / packets) if packets else 0

    if protocol == "ICMP":
        return "mMTC"
    if dst_port in _MMTC_LIKE_PORTS:
        return "mMTC"
    if dst_port in _EMBB_LIKE_PORTS and octets >= 500:
        return "eMBB"
    if avg_pkt_size < 100 and packets <= 2:
        return "mMTC"
    if octets > 5000:
        return "eMBB"
    return None


def classify_record(record: dict) -> str:
    """
    Combined classifier, tried in order of decreasing reliability:
      0. Pre-classified ground truth (e.g. TEID-based capture pipeline
         that already knows the slice) - if present, trust it directly.
      1. Standardized 5QI lookup (real 5G core QoS signal - most accurate)
      2. Port/protocol proxy (for raw NetFlow-style captures with no 5QI)
      3. Latency/throughput heuristic (for anything else with those fields)
      4. Default bucket
    """
    pre_classified = record.get("slice")
    if pre_classified in ("eMBB", "URLLC", "mMTC"):
        return pre_classified

    slice_from_5qi = classify_by_5qi(record.get("five_qi"))
    if slice_from_5qi:
        return slice_from_5qi

    if record.get("dst_port") is not None or record.get("protocol") is not None:
        slice_from_flow = classify_by_network_flow(record)
        if slice_from_flow:
            return slice_from_flow

    return classify_by_heuristics(record)


# ---------------------------------------------------------------------------
# Section 2: aggregation
# ---------------------------------------------------------------------------
@dataclass
class SliceStats:
    flow_count: int = 0
    total_throughput_kbps: float = 0.0
    latencies_ms: list = field(default_factory=list)
    gbr_flows: int = 0

    def add(self, record: dict):
        self.flow_count += 1
        self.total_throughput_kbps += record.get("throughput_kbps") or 0
        if record.get("latency_ms") is not None:
            self.latencies_ms.append(record["latency_ms"])
        five_qi = record.get("five_qi")
        entry = FIVE_QI_TABLE.get(int(five_qi)) if five_qi is not None else None
        if entry and entry[1] in ("GBR", "Delay-critical GBR"):
            self.gbr_flows += 1
        elif five_qi is None:
            sst = record.get("sst")
            if sst is not None and int(sst) == 2:
                self.gbr_flows += 1

    def to_profile(self) -> dict:
        return {
            "flow_count": self.flow_count,
            "total_throughput_kbps": round(self.total_throughput_kbps, 2),
            "avg_latency_ms": round(mean(self.latencies_ms), 2) if self.latencies_ms else None,
            "gbr_flows": self.gbr_flows,
        }


def build_slice_profiles(records: list[dict]) -> dict:
    """Classify a batch of records and roll them up into per-slice profiles."""
    stats = {"eMBB": SliceStats(), "URLLC": SliceStats(), "mMTC": SliceStats()}
    for record in records:
        slice_type = classify_record(record)
        stats[slice_type].add(record)
    return {name: s.to_profile() for name, s in stats.items()}


# ---------------------------------------------------------------------------
# Section 3: data source (swap this out for your real Airtel feed)
# ---------------------------------------------------------------------------
# Toggle with an env var so you don't have to edit code when you swap in
# real Airtel data later: USE_RANDOM_DATA=0 python3 slice_scheduler.py
USE_RANDOM_DATA = os.environ.get("USE_RANDOM_DATA", "1") == "1"

# Roughly realistic 5QI pools per slice, so the random generator produces
# a believable mix instead of pure noise.
_EMBB_5QIS = [1, 2, 4, 5, 6, 7, 66, 70]
_URLLC_5QIS = [3, 65, 67, 69, 79, 80, 82, 83, 84, 85, 86, 87, 88]
_MMTC_5QIS = [8, 9, 10]


def generate_random_batch(n: int = None) -> list[dict]:
    """
    Synthetic traffic generator for testing the pipeline before real Airtel
    data is available. Produces a realistic-ish mix of UEs across all three
    slice types, including some records with no 5QI at all (to exercise the
    heuristic fallback path) and some with unusually low/high values (to
    exercise edge cases).
    """
    n = n or random.randint(5, 25)
    records = []
    for i in range(n):
        roll = random.random()

        if roll < 0.45:  # eMBB-ish
            five_qi = random.choice(_EMBB_5QIS)
            latency_ms = random.randint(60, 300)
            throughput_kbps = random.uniform(1500, 20000)
            packet_size_bytes = random.randint(800, 1500)
            session_interval_s = None

        elif roll < 0.75:  # URLLC-ish
            five_qi = random.choice(_URLLC_5QIS)
            latency_ms = random.randint(1, 30)
            throughput_kbps = random.uniform(50, 500)
            packet_size_bytes = random.randint(64, 500)
            session_interval_s = None

        elif roll < 0.90:  # mMTC-ish
            five_qi = random.choice(_MMTC_5QIS)
            latency_ms = random.randint(200, 1200)
            throughput_kbps = random.uniform(0.5, 10)
            packet_size_bytes = random.randint(20, 150)
            session_interval_s = random.randint(300, 3600)

        else:  # no 5QI at all - forces the heuristic fallback to run
            five_qi = None
            latency_ms = random.choice([None, random.randint(1, 500)])
            throughput_kbps = random.uniform(0.5, 20000)
            packet_size_bytes = random.randint(20, 1500)
            session_interval_s = random.choice([None, random.randint(60, 3600)])

        records.append({
            "imsi": f"40410{random.randint(10**8, 10**9 - 1)}",
            "five_qi": five_qi,
            "latency_ms": latency_ms,
            "throughput_kbps": round(throughput_kbps, 2),
            "packet_size_bytes": packet_size_bytes,
            "session_interval_s": session_interval_s,
        })
    return records


def fetch_batch() -> list[dict]:
    """
    Data source entry point.

    Right now this returns synthetic traffic (USE_RANDOM_DATA=1, the
    default) so you can prove the classifier + scheduler loop end-to-end
    on your Ubuntu testbed without waiting on the Airtel dataset.

    Three modes, controlled by env vars:
      USE_RANDOM_DATA=1 (default) -> generate_random_batch()
      USE_RANDOM_DATA=0 and DATA_SOURCE_PATH set -> read your own file
      USE_RANDOM_DATA=0 and DATA_SOURCE_PATH unset -> raises, fill in
          your real ingestion (Kafka poll / DB query) yourself
    """
    if USE_RANDOM_DATA:
        return generate_random_batch()

    data_path = os.environ.get("DATA_SOURCE_PATH")
    if data_path:
        return load_batch_from_file(Path(data_path))

    # --- plug streaming/DB ingestion in here ---
    raise NotImplementedError(
        "Set USE_RANDOM_DATA=1 for testing, DATA_SOURCE_PATH=/path/to/file "
        "to read your own CSV/JSON, or implement real ingestion here."
    )


# ---------------------------------------------------------------------------
# Section 3b: feed your own test data file (CSV or JSON)
# ---------------------------------------------------------------------------
# Your file's column/field names almost certainly won't match the internal
# names (five_qi, latency_ms, throughput_kbps, ...) exactly. FIELD_ALIASES
# lets you point common alternate names at the field the classifier expects,
# without touching any classification logic. Add your own column names here
# if they differ.
FIELD_ALIASES = {
    "five_qi": ["five_qi", "5qi", "qci", "5QI", "QCI"],
    "latency_ms": ["latency_ms", "latency", "delay_ms", "rtt_ms"],
    "throughput_kbps": ["throughput_kbps", "throughput", "bitrate_kbps", "data_rate_kbps"],
    "packet_size_bytes": ["packet_size_bytes", "packet_size", "pkt_size"],
    "session_interval_s": ["session_interval_s", "session_interval", "interval_s"],
    "imsi": ["imsi", "IMSI", "subscriber_id"],
    "slice": ["slice", "Slice"],
    "sst": ["sst", "SST"],
    # Raw NetFlow / packet-capture style columns (no 5QI, no latency -
    # this is what a network tap or router export typically looks like).
    "dst_port": ["Dst Port", "dst_port", "destination_port"],
    "src_port": ["Src Port", "src_port", "source_port"],
    "packets": ["Packets", "packets"],
    "octets": ["Octets", "octets", "bytes"],
    "protocol": ["Protocol", "protocol", "proto"],
}


def _normalize_record(raw: dict) -> dict:
    """Map whatever column names your file has onto the internal field names."""
    out = {}
    lower_raw = {k.lower(): v for k, v in raw.items()}
    for internal_name, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias.lower() in lower_raw:
                out[internal_name] = lower_raw[alias.lower()]
                break

    # numeric fields: coerce to float/int, blank/missing -> None
    for numeric_field in ("five_qi", "latency_ms", "throughput_kbps",
                           "packet_size_bytes", "session_interval_s",
                           "dst_port", "src_port", "packets", "octets"):
        val = out.get(numeric_field)
        if val in (None, "", "NA", "null"):
            out[numeric_field] = None
        elif isinstance(val, str):
            try:
                out[numeric_field] = float(val) if "." in val else int(val)
            except ValueError:
                out[numeric_field] = None

    # Raw NetFlow rows don't carry packet_size_bytes/throughput_kbps directly -
    # derive them from Packets/Octets when available.
    packets, octets = out.get("packets"), out.get("octets")
    if packets and octets and not out.get("packet_size_bytes"):
        out["packet_size_bytes"] = round(octets / packets, 1)
    if octets and not out.get("throughput_kbps"):
        # Octets here is the byte volume for this flow's reporting interval.
        # Treated as ~1s window for a rough kbps estimate - a proxy, not a
        # measured rate (raw NetFlow doesn't give you real duration per row).
        out["throughput_kbps"] = round(octets * 8 / 1000, 2)

    return out


def load_batch_from_file(path: Path) -> list[dict]:
    """
    Reads your test data file (CSV or JSON array of objects) and normalizes
    it into the record shape classify_record() expects.
    """
    if not path.exists():
        raise FileNotFoundError(f"DATA_SOURCE_PATH points to a file that doesn't exist: {path}")

    if path.suffix.lower() == ".json":
        raw_records = json.loads(path.read_text())
    elif path.suffix.lower() in (".csv", ".tsv"):
        import csv
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open(newline="") as f:
            raw_records = list(csv.DictReader(f, delimiter=delimiter))
    else:
        raise ValueError(f"Unsupported file type: {path.suffix} (use .csv, .tsv, or .json)")

    records = [_normalize_record(r) for r in raw_records]
    log.info("Loaded %d records from %s", len(records), path)
    return records


# ---------------------------------------------------------------------------
# Section 4: the scheduled job
# ---------------------------------------------------------------------------
OUTPUT_PATH = Path("slice_profile.json")


def run_cycle():
    records = fetch_batch()
    if not records:
        log.info("No new records this cycle.")
        return

    profiles = build_slice_profiles(records)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "batch_size": len(records),
        "slices": profiles,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    log.info("Classified %d records -> %s", len(records), OUTPUT_PATH)
    for slice_name, profile in profiles.items():
        log.info("  %-6s flows=%-4d throughput=%.1fkbps avg_latency=%s gbr=%d",
                  slice_name, profile["flow_count"], profile["total_throughput_kbps"],
                  profile["avg_latency_ms"], profile["gbr_flows"])

    # Section 5 hook - uncomment once your kube context/RBAC is set up
    # for slice_name, profile in profiles.items():
    #     scale_upf_for_slice(slice_name, profile)


# ---------------------------------------------------------------------------
# Section 5: integration into your k3s / OAI environment
# ---------------------------------------------------------------------------
# Two integration patterns, pick one to start with:
#
# A) File/ConfigMap handoff (simplest, safest for a demo):
#    This scheduler just writes slice_profile.json every cycle (already
#    done above). A second, separate process - your MANO-side controller -
#    watches that file (or the ConfigMap it's pushed into) and decides
#    whether to scale UPF replicas or adjust SMF's slice policy. Keeping
#    classification and orchestration as separate processes means a bug
#    in the classifier can't directly touch your live UPF deployments -
#    good for a ministry/CDAC demo where you want to show each stage.
#
# B) Direct trigger (tighter loop, use once A is proven stable):
#    Call the Kubernetes API directly from this scheduler to scale the
#    UPF deployment for a slice when its load crosses a threshold.
#    Requires `pip install kubernetes --break-system-packages` and a
#    kubeconfig/service account with permission to patch deployments
#    in your k3s cluster.
#
# def scale_upf_for_slice(slice_name: str, profile: dict, high_watermark_kbps: float = 8000):
#     from kubernetes import client, config
#     config.load_kube_config()  # or config.load_incluster_config() if running inside k3s
#     apps_v1 = client.AppsV1Api()
#     deployment_name = f"upf-{slice_name.lower()}"
#     namespace = "oai-core"
#
#     desired_replicas = 2 if profile["total_throughput_kbps"] > high_watermark_kbps else 1
#     apps_v1.patch_namespaced_deployment_scale(
#         name=deployment_name,
#         namespace=namespace,
#         body={"spec": {"replicas": desired_replicas}},
#     )
#     log.info("Scaled %s to %d replicas (%.1f kbps observed)",
#               deployment_name, desired_replicas, profile["total_throughput_kbps"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    INTERVAL_SECONDS = 30  # tune to your data arrival rate

    schedule.every(INTERVAL_SECONDS).seconds.do(run_cycle)
    log.info("Slice scheduler started, running every %ds. Ctrl+C to stop.", INTERVAL_SECONDS)

    run_cycle()  # run once immediately instead of waiting for the first interval
    while True:
        schedule.run_pending()
        time.sleep(1)
