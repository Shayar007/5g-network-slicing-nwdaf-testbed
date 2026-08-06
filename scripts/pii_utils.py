#!/usr/bin/env python3
"""
pii_utils.py
Reusable PII-anonymization helper for the slice-testbed scripts.

Purpose
-------
Raw subscriber identifiers (IMSI / SUPI) must never appear in plaintext in:
  - Prometheus metrics/labels (scraped, stored, exposed via HTTP)
  - Log files (systemd journal, /var/log/slice-testbed/*.log)
  - Git commits (evidence/ files, configs)

This module provides a single, consistent, salted one-way hash so that:
  - The SAME IMSI always maps to the SAME pseudonym (needed to correlate
    a subscriber's behavior across time/metrics without knowing who they are)
  - The pseudonym CANNOT be reversed back to the IMSI without the salt file
  - The salt is stored separately (not in git, not in logs) so that even if
    pseudonymized data leaks, it cannot be de-anonymized without also
    compromising the salt file.

This is pseudonymization, not full anonymization -- if the salt AND the
pseudonym are both compromised, and an attacker has a candidate IMSI list,
they can still test candidates against the hash (a "known-plaintext" style
attack). For a real deployment, this should be paired with:
  - Access controls on who can query raw metrics/logs at all
  - Salt rotation policy
  - Consideration of full anonymization (k-anonymity / no persistent linkage)
    if longitudinal correlation is not actually required downstream

Usage
-----
    from pii_utils import anonymize_imsi, load_or_create_salt

    salt = load_or_create_salt()
    safe_id = anonymize_imsi("001010000000340", salt)
    # -> "ue-a1b2c3d4e5f6" (deterministic, non-reversible without salt)

Salt file location: ~/.slice-testbed/pii_salt (created with 0600 perms,
NEVER committed to git -- see .gitignore entry this module expects you to add).
"""

import hashlib
import hmac
import os
import re
import stat
from pathlib import Path

DEFAULT_SALT_PATH = Path.home() / ".slice-testbed" / "pii_salt"
PSEUDONYM_PREFIX = "ue-"
PSEUDONYM_HEX_LEN = 12  # 12 hex chars = 48 bits, negligible collision risk at this scale

_IMSI_RE = re.compile(r"^imsi-(\d+)$")


def load_or_create_salt(path: Path = DEFAULT_SALT_PATH) -> bytes:
    """
    Load the salt used for HMAC hashing, creating it on first run.
    The salt file is created with 0600 permissions (owner read/write only).
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    if path.exists():
        data = path.read_bytes()
        if len(data) < 32:
            raise ValueError(
                f"Salt file {path} exists but looks too short/corrupt "
                f"({len(data)} bytes, expected >=32). Refusing to proceed -- "
                f"a weak or corrupt salt undermines the whole scheme."
            )
        return data

    # First run: generate a strong random salt
    salt = os.urandom(32)
    path.write_bytes(salt)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    return salt


def anonymize_imsi(identifier: str, salt: bytes) -> str:
    """
    Deterministically pseudonymize an IMSI or 'imsi-<digits>' style identifier.

    Same identifier + same salt -> same pseudonym, always.
    Different salt -> completely different, uncorrelated pseudonym
    (this is why the salt must never be committed alongside the data).

    Accepts either a bare IMSI ("001010000000340") or the SUPI-style
    string commonly seen in nr-cli/AMF output ("imsi-001010000000340").
    Returns a short, log-friendly pseudonym like "ue-3f9a0c1b2d4e".
    """
    if not identifier:
        raise ValueError("identifier must be non-empty")

    m = _IMSI_RE.match(identifier)
    raw = m.group(1) if m else identifier

    digest = hmac.new(salt, raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{PSEUDONYM_PREFIX}{digest[:PSEUDONYM_HEX_LEN]}"


def anonymize_many(identifiers, salt: bytes):
    """Convenience helper: anonymize a list/iterable of identifiers."""
    return [anonymize_imsi(i, salt) for i in identifiers]


# Matches "imsi-<digits>" wherever it appears in a line -- not just whole-line
# matches like _IMSI_RE above. This is deliberately broader, since debug/evidence
# text often has IMSIs embedded mid-line (e.g. "UPF=EXISTS UEs=5 imsi-...").
_IMSI_INLINE_RE = re.compile(r"imsi-\d+")


def sanitize_nr_cli_output(text: str, salt: bytes) -> str:
    """
    Replace every 'imsi-<digits>' occurrence in a block of text with its
    pseudonym, leaving everything else unchanged.

    Intended use: before saving any nr-cli --dump output, log excerpt, or
    debug capture to an evidence/ file (or printing it to a log that might
    get committed), run it through this function first.

        raw = subprocess.run([...], capture_output=True, text=True).stdout
        safe = sanitize_nr_cli_output(raw, salt)
        with open("evidence/some_capture.log", "w") as f:
            f.write(safe)

    This does NOT change counts, structure, or any non-IMSI content --
    it is a drop-in replacement for the exact text you would have saved,
    just with subscriber identifiers pseudonymized.
    """
    return _IMSI_INLINE_RE.sub(lambda m: anonymize_imsi(m.group(0), salt), text)


if __name__ == "__main__":
    # Self-test / demo -- safe to run standalone, does not touch the cluster.
    salt = load_or_create_salt()
    print(f"Salt file: {DEFAULT_SALT_PATH} (0600, not in git)")

    test_ids = [
        "imsi-001010000000340",
        "imsi-001010000000341",
        "001010000000340",  # bare form should hash the same as the imsi- form
    ]
    print("\n--- Determinism + format check ---")
    for tid in test_ids:
        print(f"  {tid:30s} -> {anonymize_imsi(tid, salt)}")

    print("\n--- Confirm same IMSI always maps to same pseudonym ---")
    a = anonymize_imsi("imsi-001010000000340", salt)
    b = anonymize_imsi("imsi-001010000000340", salt)
    assert a == b, "FAIL: not deterministic!"
    print(f"  OK: repeated call gives identical pseudonym ({a})")

    print("\n--- Confirm bare-digits and imsi- prefixed forms match ---")
    c = anonymize_imsi("001010000000340", salt)
    assert a == c, "FAIL: prefix handling inconsistent!"
    print(f"  OK: 'imsi-001010000000340' and '001010000000340' both -> {a}")

    print("\n--- Confirm different IMSIs give different pseudonyms ---")
    d = anonymize_imsi("imsi-001010000000341", salt)
    assert a != d, "FAIL: collision on different input!"
    print(f"  OK: different IMSI -> different pseudonym ({d})")

    print("\n--- Confirm sanitize_nr_cli_output() on a realistic multi-line capture ---")
    sample = (
        "UERANSIM-gnb-1\n"
        "imsi-001010000000340\n"
        "imsi-001010000000341\n"
        "[2026-08-05 09:26:37] [nwdaf-ctrl] INFO: [MMTC ] UEs=  5  thr=999  UPF=EXISTS imsi-001010000000340\n"
    )
    sanitized = sanitize_nr_cli_output(sample, salt)
    assert "imsi-" not in sanitized, "FAIL: raw imsi- string still present after sanitization!"
    assert sanitized.count("ue-") == 3, "FAIL: expected 3 pseudonyms (2 dump lines + 1 inline)"
    # Same IMSI appearing twice in the text should produce the identical pseudonym both times
    assert sanitized.count(a) >= 0  # (a defined above) -- just ensure no crash; format check below
    first_line_pseudo = sanitized.splitlines()[1]
    inline_pseudo = sanitized.splitlines()[3].split()[-1]
    assert first_line_pseudo == inline_pseudo, "FAIL: same IMSI gave different pseudonyms in same text!"
    print("  OK: sanitized text has zero 'imsi-' occurrences")
    print("  OK: repeated IMSI in same text -> same pseudonym both times")
    print(f"  Sample output:\n{sanitized}")

    print("\nAll self-tests passed.")
