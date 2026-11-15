"""Job store in out/studio.db: generation jobs, and the review steps that check and fix each final.

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
# layout: the picked sketch (a drafts job's file) a final is built on. review: where a final's review stands
# (REVIEW_STATES); review_reason: the plain words shown with "needs a person".
ADDED_COLUMNS = {"layout": "TEXT", "review": "TEXT", "review_reason": "TEXT"}

# --- review steps: the automatic check-and-fix chain of one final job (studio.worker) ------------
STEP_KINDS = ("check", "finish", "repair", "regenerate", "composite")
STEP_STATUSES = ("queued", "running", "done", "failed")
STEP_JSON = ("result",)
STEP_SETTABLE = {"output", "result", "error", "cost_usd"}
REVIEW_STATES = ("checking", "fixing", "ready", "needs_person", "approved", "rejected", "anchor")
VERDICTS = ("approved", "rejected", "anchor")  # a person's decision: the automatic chain never changes it
REVIEW_SETTABLE = {"reason"}
INTERRUPTED = "The server stopped during an automatic check or fix; please look."
NOT_CHECKED = "The automatic checks did not run on this image; please look."
STEPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_steps (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    kind TEXT NOT NULL CHECK (kind IN ('check', 'finish', 'repair', 'regenerate', 'composite')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    input TEXT NOT NULL,
    output TEXT,
    code TEXT,
    result TEXT,
    error TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
)"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute(SCHEMA)
        conn.execute(STEPS_SCHEMA)
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
    """At start-up nothing a stopped server left running is re-run: a job becomes interrupted, a review step
    fails, and a review left checking or fixing with no step queued needs a person. So does a done final that
    was never checked (no review, no steps): one from before the review chain.
    Returns how many jobs and steps were running."""
    t = _now()
    with conn:
        jobs = conn.execute("UPDATE jobs SET status = 'interrupted', updated = ? WHERE status = 'running'",
                            (t,)).rowcount
        steps = conn.execute("UPDATE review_steps SET status = 'failed', error = ?, updated = ?"
                             " WHERE status = 'running'", ("interrupted by a restart; not re-run", t)).rowcount
        conn.execute("UPDATE jobs SET review = 'needs_person', review_reason = ?, updated = ?"
                     " WHERE review IN ('checking', 'fixing') AND NOT EXISTS (SELECT 1 FROM review_steps s"
                     " WHERE s.job_id = jobs.id AND s.status = 'queued')", (INTERRUPTED, t))
        conn.execute("UPDATE jobs SET review = 'needs_person', review_reason = ?, updated = ?"
                     " WHERE kind = 'final' AND status = 'done' AND review IS NULL AND NOT EXISTS"
                     " (SELECT 1 FROM review_steps s WHERE s.job_id = jobs.id)", (NOT_CHECKED, t))
    return jobs + steps


def complete_final(conn, job_id: int, result: dict, check_input: str) -> None:
    """A final job done, the check of its file queued and its review "checking", in one transaction: a crash
    never leaves a done final with no check and no review."""
    t = _now()
    with conn:
        conn.execute("UPDATE jobs SET status = 'done', result = ?, updated = ? WHERE id = ?",
                     (json.dumps(result), t, job_id))
        conn.execute("INSERT INTO review_steps (job_id, kind, status, input, created, updated)"
                     " VALUES (?, 'check', 'queued', ?, ?, ?)", (job_id, check_input, t, t))
        conn.execute("UPDATE jobs SET review = 'checking', review_reason = NULL WHERE id = ?"
                     " AND (review IS NULL OR review IN ('checking', 'fixing', 'ready', 'needs_person'))", (job_id,))


# --- review steps ------------------------------------------------------------------------------
def _step(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in STEP_JSON:
        d[k] = None if d[k] is None else json.loads(d[k])
    return d


def add_step(conn, job_id: int, kind: str, input: str, code: str | None = None) -> int:
    """Queue one step of a final job's review chain, on the image at repo-relative `input`."""
    if kind not in STEP_KINDS:
        raise ValueError(f"unknown review step kind {kind!r}")
    t = _now()
    with conn:
        cur = conn.execute("INSERT INTO review_steps (job_id, kind, status, input, code, created, updated)"
                           " VALUES (?, ?, 'queued', ?, ?, ?, ?)", (job_id, kind, input, code, t, t))
    return cur.lastrowid


def next_step(conn) -> dict | None:
    """The oldest queued step, once no job is queued: a queued generation job always goes first."""
    return _step(conn.execute(
        "SELECT * FROM review_steps WHERE status = 'queued'"
        " AND NOT EXISTS (SELECT 1 FROM jobs WHERE status = 'queued') ORDER BY id LIMIT 1").fetchone())


def claim_step(conn, step_id: int) -> bool:
    """queued -> running in one statement, so no step is ever taken twice."""
    with conn:
        cur = conn.execute("UPDATE review_steps SET status = 'running', updated = ? WHERE id = ? AND status = 'queued'",
                           (_now(), step_id))
    return cur.rowcount == 1


def set_step(conn, step_id: int, status: str, **fields) -> None:
    if status not in STEP_STATUSES:
        raise ValueError(f"unknown step status {status!r}")
    if set(fields) - STEP_SETTABLE:
        raise ValueError(f"cannot set {sorted(set(fields) - STEP_SETTABLE)}")
    vals = {k: json.dumps(v) if k in STEP_JSON else v for k, v in fields.items()}
    cols = ", ".join(f"{k} = ?" for k in ("status", "updated", *vals))
    with conn:
        conn.execute(f"UPDATE review_steps SET {cols} WHERE id = ?", (status, _now(), *vals.values(), step_id))


def steps_for(conn, job_id: int) -> list[dict]:
    """A job's review steps, oldest first."""
    return [_step(r) for r in conn.execute("SELECT * FROM review_steps WHERE job_id = ? ORDER BY id", (job_id,))]


def set_review(conn, job_id: int, state: str, only_from: tuple | None = None, **fields) -> bool:
    """Set a job's review state. `reason` (the plain words for needs_person) is replaced every time.
    only_from: set it only while the review is one of these states (None standing for "not started yet"), in one
    statement, so of two writers racing from the same state exactly one succeeds. True when the state was set."""
    if state not in REVIEW_STATES:
        raise ValueError(f"unknown review state {state!r}")
    if set(fields) - REVIEW_SETTABLE:
        raise ValueError(f"cannot set {sorted(set(fields) - REVIEW_SETTABLE)}")
    sql = "UPDATE jobs SET review = ?, review_reason = ?, updated = ? WHERE id = ?"
    args = [state, fields.get("reason"), _now(), job_id]
    if only_from is not None:
        named = [s for s in only_from if s is not None]
        tests = [f"review IN ({', '.join('?' * len(named))})"] if named else []
        tests += ["review IS NULL"] if None in only_from else []
        sql += f" AND ({' OR '.join(tests) or '0'})"
        args += named
    with conn:
        return conn.execute(sql, args).rowcount == 1
