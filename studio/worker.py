"""Background worker: runs queued jobs one at a time through the existing pipeline.

Each job is routed again when it runs, so every budget rule applies at spend time. No retries:
a failed job stays failed, and a job cut off by a restart is marked interrupted by db.mark_interrupted.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Callable

from pipeline import generate, ledger, router
from pipeline.compile import ROOT, Compiled, compile_spec

from . import db
from .brain.base import scrub

DRAFTS = 3  # Cloudflare drafts per drafts job
POLL_S = 1.0
log = logging.getLogger("studio.worker")


def routes_for(c: Compiled, kind: str) -> list[router.Route]:
    """final: one route. drafts: DRAFTS routes, each counting the ones before it."""
    if kind == "final":
        return [router.route(c)]
    if kind == "drafts":
        pending, routes = router.Pending(), []
        for _ in range(DRAFTS):
            r = router.route(c, "draft", pending)
            pending.add(r)
            routes.append(r)
        return routes
    raise ValueError(f"unknown job kind {kind!r}")


def _rel(path: Path) -> str:
    path = Path(path)
    return (path.relative_to(ROOT) if path.is_absolute() else path).as_posix()


class Worker:
    """runner(c, route) -> (path, result with cost_usd); None means generate.run_route, looked up per call."""

    def __init__(self, connect_fn: Callable[[], sqlite3.Connection] = db.connect,
                 runner: Callable | None = None, poll_s: float = POLL_S):
        self.connect_fn, self.runner, self.poll_s = connect_fn, runner, poll_s
        self._stop = threading.Event()

    def run_once(self) -> bool:
        """Run the oldest queued job, if any. True when a job was taken."""
        conn = self.connect_fn()
        try:
            job = db.next_queued(conn)
            if job is None or not db.claim(conn, job["id"]):
                return False
            result = {"files": [], "cost_usd": 0.0}  # filled as each call returns
            try:
                self._run(job, result)
            except Exception as e:  # the job ends here; nothing re-queues it
                # keep what was already made and paid for (e.g. 2 of 3 drafts); None if nothing was
                db.set_status(conn, job["id"], "failed", error=scrub(str(e) or type(e).__name__, limit=1000),
                              result=result if result["files"] else None)
            else:
                db.set_status(conn, job["id"], "done", result=result)
            return True
        finally:
            conn.close()

    def _run(self, job: dict, result: dict) -> dict:
        """Fill `result` ({"files", "cost_usd"}) call by call, so a failure part-way keeps what was made."""
        c = compile_spec(job["spec"])
        routes = routes_for(c, job["kind"])
        blocked = next((r for r in routes if r.action != "run"), None)
        if blocked:
            raise ledger.BudgetError(blocked.reason or f"route is {blocked.action!r}")
        total = sum(r.estimate.get("cost_usd", 0.0) for r in routes)
        if total > ledger.RUN_APPROVAL_USD and not job["confirmed_by"]:
            raise ledger.BudgetError(f"run estimated at ${total:.2f} needs confirmation")
        runner = self.runner or generate.run_route
        for r in routes:
            path, res = runner(c, r)
            result["files"].append(_rel(path))
            result["cost_usd"] = round(result["cost_usd"] + res.cost_usd, 4)
        return result

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self._loop, name="studio-worker", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                took = self.run_once()
            except Exception:  # e.g. database locked: wait and look again; the job itself is not retried
                log.exception("worker pass failed")
                took = False
            if not took:
                self._stop.wait(self.poll_s)
