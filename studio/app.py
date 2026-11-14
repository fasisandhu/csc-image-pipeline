"""Local JSON API for the dashboard: questions, brief, preview with cost, and generation jobs.

Served on 127.0.0.1 only (python -m studio). Endpoints are plain def: brain calls are synchronous
and run their own event loop. Jobs are only queued here; studio.worker routes them again at spend
time, so the pipeline keeps enforcing every budget rule.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from typing import Literal

from fastapi import Depends, FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictBool

from pipeline import ledger
from pipeline.compile import CompileError, compile_spec, load_json
from pipeline.providers import ProviderError
from pipeline.specs import build_spec, find_anchor

from . import db
from .brain import BrainError, get_brain
from .identity import current_user
from .questions import missing, resolve, valid_value
from .tasks import read_brief
from .worker import routes_for

app = FastAPI(title="CSC Studio", dependencies=[Depends(current_user)])
# DNS-rebinding guard: a page on another domain that resolves to 127.0.0.1 is refused.
# "testserver" is the TestClient's host name.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


class BadAnswers(ValueError):
    """Answers that are not valid choices from the question set."""


class AnswersIn(BaseModel):
    answers: dict = Field(default_factory=dict)


class BriefIn(AnswersIn):
    text: str


class JobIn(AnswersIn):
    kind: Literal["final", "drafts"] = "final"
    confirm: StrictBool = False


# --- answers: only valid choices from the question set reach the pipeline --------------------
def _type_ok(key: str, value) -> bool:
    """A scalar, or for colour only a custom {"name", "hex"} of strings."""
    if key == "colour" and isinstance(value, dict):
        return set(value) == {"name", "hex"} and all(isinstance(v, str) for v in value.values())
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def _normal(answers: dict) -> dict:
    return {**answers, "gsm": int(answers["gsm"])} if "gsm" in answers else answers  # option id "450" -> 450


def checked(answers: dict) -> dict:
    """Complete answers, every one a valid choice; otherwise BadAnswers naming the first field that is not."""
    for k, v in answers.items():  # types first: the question resolver expects scalars
        if not _type_ok(k, v):
            raise BadAnswers(f"invalid answer for {k}")
    for k, v in answers.items():
        if not valid_value(k, v, answers):
            raise BadAnswers(f"invalid answer for {k}")
    gaps = missing(answers)
    if gaps:
        raise BadAnswers(f"missing answers: {', '.join(gaps)}")
    return _normal(answers)


def usable(answers: dict) -> dict:
    """The valid part of in-progress answers. Repeats: a value may depend on one just dropped."""
    clean = {k: v for k, v in answers.items() if _type_ok(k, v)}
    while bad := [k for k, v in clean.items() if not valid_value(k, v, clean)]:
        for k in bad:
            del clean[k]
    return _normal(clean)


def needs_anchor(answers: dict) -> bool:
    """Reported only for now; enforced in plan 2."""
    return (answers["recipe"] != "packshot-front"
            and find_anchor(answers["catalogue"], answers["colour"], answers["gsm"]) is None)


def estimate(spec: dict, kind: str = "final") -> dict:
    """Router cost of a job: this call (base) and with every allowed regeneration and repair (worst)."""
    routes = routes_for(compile_spec(spec), kind)
    r = next((x for x in routes if x.action != "run"), routes[0])
    base = round(sum(x.estimate.get("cost_usd", 0.0) for x in routes), 4)
    worst = base
    if kind == "final":
        lim = load_json("brand/repairs.json")["limits"]
        R, P = lim["regenerations_per_shot"], lim["repairs_per_render"]
        worst = round(base * (1 + R) + base * P * (1 + R), 2)
    return {"base_usd": base, "worst_usd": worst, "action": r.action, "reason": r.reason,
            "tier": r.tier, "model": r.model}


# --- errors ----------------------------------------------------------------------------------
@app.exception_handler(BadAnswers)
@app.exception_handler(CompileError)
async def _unprocessable(request, e: Exception):
    return JSONResponse({"error": str(e)}, 422)


@app.exception_handler(BrainError)
@app.exception_handler(ProviderError)
async def _unavailable(request, e: Exception):  # messages carry no credential values
    return JSONResponse({"error": str(e)}, 503)


# --- endpoints -------------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "budget": ledger.status()}


@app.post("/api/questions")
def questions(body: AnswersIn):
    answers = usable(body.answers)
    return {"sections": [asdict(s) for s in resolve(answers)], "missing": missing(answers)}


@app.post("/api/brief")
def brief(body: BriefIn, user: str = Depends(current_user)):
    return read_brief(body.text, get_brain(user, fast=True), usable(body.answers), user=user)


@app.post("/api/preview")
def preview(body: AnswersIn):
    answers = checked(body.answers)
    spec = build_spec(answers)
    c = compile_spec(spec)
    est = estimate(spec)
    return {"shot_id": c.shot_id, "prompt": c.prompts["paragraph"], "warnings": c.warnings,
            **{k: est[k] for k in ("base_usd", "worst_usd", "action", "reason")},
            "needs_anchor": needs_anchor(answers)}


@app.post("/api/jobs", status_code=201)
def create_job(body: JobIn, user: str = Depends(current_user)):
    answers = checked(body.answers)
    spec = build_spec(answers)
    est = estimate(spec, body.kind)
    if est["action"] != "run":
        return JSONResponse({"error": est["reason"] or f"route is {est['action']!r}"}, 409)
    if est["base_usd"] > ledger.RUN_APPROVAL_USD and not body.confirm:
        return JSONResponse({"error": f"estimated ${est['base_usd']:.2f} is over "
                                      f"${ledger.RUN_APPROVAL_USD:.2f}; confirm to run"}, 409)
    with closing(db.connect()) as conn:
        job_id = db.create_job(conn, user, body.kind, answers, spec, est,
                               confirmed_by=user if body.confirm else None)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    with closing(db.connect()) as conn:
        job = db.get_job(conn, job_id)
    return job if job is not None else JSONResponse({"error": f"job {job_id} not found"}, 404)
