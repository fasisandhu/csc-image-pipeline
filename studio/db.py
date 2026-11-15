"""Job store: one SQLite table of generation jobs in out/studio.db.

A connection never crosses threads: each request and each worker pass opens, uses and
closes its own. answers, spec, estimate and result are stored as JSON text.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pipeline import ledger
from pipeline.compile import ROOT

DB_PATH = ROOT / "out" / "studio.db"
STATUSES = ("queued", "running", "done", "failed", "interrupted")
JSON_COLS = ("answers", "spec", "estimate", "result")
SETTABLE = {"result", "error", "confirmed_by"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    user TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('final', 'drafts')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'interrupted')),
    answers TEXT NOT NULL,
    spec TEXT NOT NULL,
    estimate TEXT NOT NULL,
    result TEXT,
    error TEXT,
    confirmed_by TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
)"""
# Columns added after plan 1. connect() adds any that are missing, so old databases keep working.
ADDED_COLUMNS = {"layout": "TEXT"}  # layout: the picked sketch (a drafts job's file) a final is built on


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute(SCHEMA)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        for col, typ in ADDED_COLUMNS.items():
            if col in have:
                continue
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError as e:  # another connection added it first
                if "duplicate column" not in str(e):
                    raise
    return conn


def _now() -> str:
    return ledger.now().isoformat(timespec="seconds")


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS:
        d[k] = None if d[k] is None else json.loads(d[k])
    return d


def create_job(conn, user: str, kind: str, answers: dict, spec: dict, estimate: dict,
               confirmed_by: str | None = None, layout: str | None = None) -> int:
    t = _now()
    with conn:
        cur = conn.execute(
            "INSERT INTO jobs (user, kind, status, answers, spec, estimate, confirmed_by, layout, created, updated)"
            " VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)",
            (user, kind, json.dumps(answers), json.dumps(spec), json.dumps(estimate), confirmed_by, layout, t, t))
    return cur.lastrowid


def get_job(conn, job_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def list_jobs(conn, user: str, limit: int = 20) -> list[dict]:
    """This user's jobs, newest first."""
    rows = conn.execute("SELECT * FROM jobs WHERE user = ? ORDER BY id DESC LIMIT ?", (user, limit))
    return [_row(r) for r in rows]


def sketch_files(conn, user: str, shot_id: str) -> list[str]:
    """Files of this user's finished drafts jobs for this shot: the sketches a final may use as its layout."""
    rows = conn.execute("SELECT * FROM jobs WHERE user = ? AND kind = 'drafts' AND status = 'done'", (user,))
    return [f for job in map(_row, rows) if job["spec"]["shot"]["shot_id"] == shot_id
            for f in (job["result"] or {}).get("files", [])]


def next_queued(conn) -> dict | None:
    return _row(conn.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone())


def claim(conn, job_id: int) -> bool:
    """queued -> running in one statement, so no job is ever taken twice."""
    with conn:
        cur = conn.execute("UPDATE jobs SET status = 'running', updated = ? WHERE id = ? AND status = 'queued'",
                           (_now(), job_id))
    return cur.rowcount == 1


def set_status(conn, job_id: int, status: str, **fields) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown job status {status!r}")
    if set(fields) - SETTABLE:
        raise ValueError(f"cannot set {sorted(set(fields) - SETTABLE)}")
    vals = {k: json.dumps(v) if k in JSON_COLS else v for k, v in fields.items()}
    cols = ", ".join(f"{k} = ?" for k in ("status", "updated", *vals))
    with conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (status, _now(), *vals.values(), job_id))


def mark_interrupted(conn) -> int:
    """At start-up: jobs left running by a stopped server become interrupted, never re-run."""
    with conn:
        return conn.execute("UPDATE jobs SET status = 'interrupted', updated = ? WHERE status = 'running'",
                            (_now(),)).rowcount
