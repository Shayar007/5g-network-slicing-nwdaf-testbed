#!/usr/bin/env python3
"""
metrics_store.py
Minimal SQLite time-series store.

WHY THIS EXISTS:
Prometheus (via pushgateway) only holds current/short-window values well
enough for dashboards, but the forecaster needs a clean, queryable append-
only history it fully controls (no scrape-interval gaps, no pushgateway
staleness quirks). This module is that history.

Read-only from the rest of the system's point of view except for
history_writer.py, which is the ONLY writer. Everyone else (analytics,
forecaster) only reads.
"""

import sqlite3
import time
from contextlib import contextmanager

from config import CFG

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    ts     REAL NOT NULL,
    slice  TEXT NOT NULL,
    metric TEXT NOT NULL,
    value  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_lookup ON metrics (slice, metric, ts);
"""


@contextmanager
def _conn():
    c = sqlite3.connect(CFG.DB_PATH, timeout=10)
    try:
        yield c
    finally:
        c.close()


def init_db():
    with _conn() as c:
        c.executescript(_SCHEMA)
        c.commit()


def write_sample(slice_name, metric, value, ts=None):
    ts = ts if ts is not None else time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO metrics (ts, slice, metric, value) VALUES (?, ?, ?, ?)",
            (ts, slice_name, metric, value),
        )
        c.commit()


def write_samples_bulk(rows):
    """rows: iterable of (ts, slice, metric, value)"""
    with _conn() as c:
        c.executemany(
            "INSERT INTO metrics (ts, slice, metric, value) VALUES (?, ?, ?, ?)",
            rows,
        )
        c.commit()


def read_series(slice_name, metric, since_seconds):
    """Returns list of (ts, value) ordered oldest -> newest, within the
    last `since_seconds`."""
    cutoff = time.time() - since_seconds
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, value FROM metrics "
            "WHERE slice = ? AND metric = ? AND ts >= ? "
            "ORDER BY ts ASC",
            (slice_name, metric, cutoff),
        ).fetchall()
    return rows


def latest_value(slice_name, metric):
    with _conn() as c:
        row = c.execute(
            "SELECT ts, value FROM metrics WHERE slice = ? AND metric = ? "
            "ORDER BY ts DESC LIMIT 1",
            (slice_name, metric),
        ).fetchone()
    return row  # (ts, value) or None


def prune_older_than(seconds):
    """Housekeeping: call periodically so the DB doesn't grow unbounded."""
    cutoff = time.time() - seconds
    with _conn() as c:
        c.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        c.commit()


if __name__ == "__main__":
    init_db()
    print(f"Initialized DB at {CFG.DB_PATH}")
