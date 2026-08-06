#!/usr/bin/env python3
"""
pii_utils.py
Shared PII-protection utility for the slice testbed. When real subscriber
identities (IMSI/SUPI) ever replace the synthetic ones used in this
testbed, raw identities must never reach logs, Prometheus labels, or git.

This module provides a one-way, salted hash for IMSIs, plus a display
helper that only ever shows a masked suffix. Import this in any script
that currently handles raw IMSI strings (ue_count_exporter.py,
controller.py, predictive_prescaler.py) BEFORE those scripts are pointed
at real subscriber data.

Usage:
    from pii_utils import anonymize_imsi, mask_imsi_for_display
    safe_id = anonymize_imsi(raw_imsi)          # for metric labels / storage
    display = mask_imsi_for_display(raw_imsi)   # for human-readable logs
"""

import hashlib
import os

# In production, load this from a secrets manager / env var injected by
# the deployment platform -- NEVER hardcode or commit a real salt.
_SALT = os.getenv("IMSI_ANONYMIZATION_SALT")

if _SALT is None:
    import sys
    print(
        "WARNING: IMSI_ANONYMIZATION_SALT is not set. Falling back to a "
        "fixed development salt. This is UNSAFE for any real subscriber "
        "data -- set IMSI_ANONYMIZATION_SALT before pointing this testbed "
        "at production identities.",
        file=sys.stderr,
    )
    _SALT = "dev-only-insecure-salt-do-not-use-in-production"


def anonymize_imsi(raw_imsi: str) -> str:
    """
    One-way salted hash of an IMSI/SUPI, safe to use as a Prometheus label,
    log field, or storage key. Not reversible; the same raw IMSI always
    hashes to the same value (needed for consistent per-subscriber
    aggregation), but the hash cannot be used to recover the original
    identity without the salt, which must be kept out of version control.
    """
    if not raw_imsi:
        return "unknown"
    digest = hashlib.sha256((_SALT + raw_imsi).encode("utf-8")).hexdigest()
    return f"anon-{digest[:16]}"


def mask_imsi_for_display(raw_imsi: str) -> str:
    """
    Human-readable masked form for logs/dashboards: only the last 3 digits
    are shown, matching common telecom-industry masking convention. Use
    this instead of printing the full IMSI anywhere a human might read it
    (terminal output, log files, tickets).
    """
    if not raw_imsi or len(raw_imsi) < 4:
        return "***"
    return "*" * (len(raw_imsi) - 3) + raw_imsi[-3:]


if __name__ == "__main__":
    # Quick self-test
    test_imsi = "001010000000340"
    print(f"raw:      {test_imsi}")
    print(f"anon:     {anonymize_imsi(test_imsi)}")
    print(f"display:  {mask_imsi_for_display(test_imsi)}")
    print(f"anon again (should match): {anonymize_imsi(test_imsi)}")
