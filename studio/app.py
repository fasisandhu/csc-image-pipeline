"""Local JSON API for the dashboard: questions, brief, preview with cost, and generation jobs.
Also serves the builder page (/ and /static) and the output images it shows (/api/files).

Served on 127.0.0.1 only (python -m studio). Endpoints are plain def: brain calls are synchronous
and run their own event loop. Jobs are only queued here; studio.worker routes them again at spend
time, so the pipeline keeps enforcing every budget rule.
"""
from __future__ import annotations

import logging
import re
from contextlib import closing
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool

from pipeline import ledger
from pipeline.compile import ROOT, CompileError, compile_spec, load_json
from pipeline.providers import ProviderError
from pipeline.specs import build_spec, find_anchor, load_recipes

from . import db, guide
from .brain import BrainError, get_brain
from .identity import current_user
from .notes import plain_warnings
from .questions import missing, resolve, valid_value
from .tasks import read_brief
from .worker import routes_for

STATIC_DIR = Path(__file__).resolve().parent / "static"
IMAGE_DIRS = (ROOT / "out" / "drafts", ROOT / "out" / "finals")  # the only files the browser may fetch
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_JOBS = 100  # most jobs one list request returns
ANCHOR_FIRST = ("This colour has no approved reference packshot yet. "
                "Make it first, then this shot can use it.")
PICK_A_SKETCH = "pick a sketch from this shot"
NO_SKETCH = "Reference packshots are made without a sketch, so the garment comes only from our catalogue."
BRAIN_BUSY = "Our AI helpers are busy right now; pick the options below."
SERVICE_DOWN = "The image service is not available right now; try again in a moment."
FOLDERS = re.compile(r"(?:[\w.:~-]*[/\\])+(?=[\w.~-])")  # the folder part of a path, "/" or "\"
SETTING_NAMES = re.compile(r"\b(?=STUDIO_|[A-Z0-9_]*(?:KEY|TOKEN|SECRET|ACCOUNT_ID))[A-Z][A-Z0-9_]{2,}\b")
log = logging.getLogger(__name__)

app = FastAPI(title="CSC Studio", dependencies=[Depends(current_user)])
# DNS-rebinding guard: a page on another domain that resolves to 127.0.0.1 is refused.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


class LocalStaticFiles(StaticFiles):
    """The page's files, under the same identity rule as the API: a mount skips the app's dependencies."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            current_user(Request(scope))  # raises 403 for anyone but this PC
        await super().__call__(scope, receive, send)


app.mount("/static", LocalStaticFiles(directory=STATIC_DIR), name="static")


class BadAnswers(ValueError):
    """Answers that are not valid choices from the question set."""


class AnswersIn(BaseModel):
    answers: dict = Field(default_factory=dict)


class BriefIn(AnswersIn):
    text: str


class JobIn(AnswersIn):
    kind: Literal["final", "drafts"] = "final"
    confirm: StrictBool = False
    layout: str | None = None  # a final only: one of this user's finished sketches of this shot


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
    """True when this shot needs an approved reference packshot that does not exist yet.
    Preview reports it; POST /api/jobs refuses a final until the packshot exists (design call C1)."""
    return (answers["recipe"] != "packshot-front"
            and find_anchor(answers["catalogue"], answers["colour"], answers["gsm"]) is None)


def anchor_answers(answers: dict) -> dict:
    """The plain front packshot of the same garment: the shot that becomes the missing anchor."""
    return {**{k: answers[k] for k in ("purpose", "catalogue", "colour", "gsm")},
            "recipe": "packshot-front", "decoration": "none"}


def takes_sketch(answers: dict) -> bool:
    """Whether a final of this shot may build on a sketch. A plain front packshot may not: it can become its
    colour's reference packshot, which later shots copy as "the exact garment", so a sketch's garment errors
    would spread to all of them. Its garment comes only from the catalogue (final review I1c)."""
    return not (answers["recipe"] == "packshot-front" and answers.get("decoration") == "none")


def picked_sketch(user: str, kind: str, layout: str, shot_id: str) -> str | None:
    """The stored path of the sketch `layout` names, or None. Only a final takes one, and only a file of
    this user's finished drafts job for this same shot. The client's string is compared, never opened."""
    if kind != "final":
        return None
    want = PurePosixPath(layout).as_posix()
    with closing(db.connect()) as conn:
        files = db.sketch_files(conn, user, shot_id)
    return next((f for f in files if PurePosixPath(f).as_posix() == want), None)


def file_names_only(text: str) -> str:
    """Browser-facing text names a file but never its folder: "brand/designs/x_ref.png" -> "x_ref.png"."""
    return FOLDERS.sub("", text)


def public_text(text: str) -> str:
    """file_names_only, and a setting's name (STUDIO_*, or one with KEY, TOKEN or SECRET) becomes "a setting"."""
    return SETTING_NAMES.sub("a setting", file_names_only(text))


def caption_for(answers: dict) -> str:
    """The caption every displayed image carries (brand.json): the on-model one when the shot has a model."""
    captions = load_json("brand/brand.json")["captions"]
    recipe = load_recipes()["recipes"].get((answers or {}).get("recipe")) or {}
    return captions["on_model"] if recipe.get("model") is True else captions["concept_render"]


def public_job(job: dict) -> dict:
    """A job as the browser gets it. The spec stays in the database (it holds repo paths such as
    refs/anchors/ and brand/designs/); its shot id comes instead, and the error loses its folders."""
    out = {k: v for k, v in job.items() if k != "spec"}
    out["shot_id"] = ((job.get("spec") or {}).get("shot") or {}).get("shot_id")
    out["caption"] = caption_for(job.get("answers"))
    if out.get("error"):
        out["error"] = public_text(out["error"])
    return out


def servable(path: str) -> Path | None:
    """The image at repo-relative `path` if the browser may have it, else None: a plain relative "/" path
    with no "..", that resolves to a file under out/drafts or out/finals with an image suffix."""
    if (not path or path.startswith("/") or any(ch in path for ch in "\\:\x00")
            or ".." in path.split("/")):
        return None
    try:
        p = (ROOT / path).resolve()
        inside = any(p.is_relative_to(d.resolve()) for d in IMAGE_DIRS)
        return p if inside and p.suffix.lower() in IMAGE_TYPES and p.is_file() else None
    except (OSError, ValueError):
        return None


def estimate(spec: dict, kind: str = "final", layout_refs: list[str] = ()) -> dict:
    """Router cost of a job: this call (base) and with every allowed regeneration and repair (worst).
    layout_refs: a final's picked sketch, costed as one more reference image."""
    routes = routes_for(compile_spec(spec, list(layout_refs)), kind)
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
# A 503 body is fixed text: a brain or provider message can name a credential type, a variable or a CLI
# path, none of which may reach the browser (final review I2).
@app.exception_handler(BadAnswers)
@app.exception_handler(CompileError)
async def _unprocessable(request, e: Exception):
    return JSONResponse({"error": public_text(str(e))}, 422)


@app.exception_handler(BrainError)
async def _brain_unavailable(request, e: Exception):  # each brain recorded its own failure in out/log.jsonl
    return JSONResponse({"error": BRAIN_BUSY}, 503)


@app.exception_handler(ProviderError)
async def _provider_unavailable(request, e: Exception):
    log.warning("%s: %s", request.url.path, e)  # for the server's console only; messages carry no key values
    return JSONResponse({"error": SERVICE_DOWN}, 503)


# --- endpoints -------------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "budget": ledger.status(),
            "gemini_usd": ledger.gemini_month_spend(), "cap_usd": ledger.GEMINI_MONTHLY_CAP_USD,
            "cf_neurons": ledger.cf_day_neurons(), "cf_cap": ledger.CF_DAILY_NEURON_CAP}


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
    return {"shot_id": c.shot_id, "prompt": c.prompts["paragraph"],
            "warnings": [public_text(w) for w in plain_warnings(c.warnings)],
            **{k: est[k] for k in ("base_usd", "worst_usd", "action", "reason", "tier")},
            "needs_anchor": needs_anchor(answers), "takes_sketch": takes_sketch(answers)}


@app.post("/api/jobs", status_code=201)
def create_job(body: JobIn, user: str = Depends(current_user)):
    answers = checked(body.answers)
    spec = build_spec(answers)
    layout = None
    if body.layout is not None:
        if body.kind == "final" and not takes_sketch(answers):
            return JSONResponse({"error": NO_SKETCH}, 422)
        layout = picked_sketch(user, body.kind, body.layout, spec["shot"]["shot_id"])
        if layout is None:
            return JSONResponse({"error": PICK_A_SKETCH}, 422)
    if body.kind == "final" and needs_anchor(answers):  # sketches are free and never published: not gated
        return JSONResponse({"error": ANCHOR_FIRST, "needs_anchor": True,
                             "anchor_answers": anchor_answers(answers)}, 409)
    est = estimate(spec, body.kind, [layout] if layout else [])
    if est["action"] != "run":
        return JSONResponse({"error": est["reason"] or f"route is {est['action']!r}"}, 409)
    if est["base_usd"] > ledger.RUN_APPROVAL_USD and not body.confirm:  # the page asks in-page, then resends
        return JSONResponse({"error": f"estimated ${est['base_usd']:.2f} is over "
                                      f"${ledger.RUN_APPROVAL_USD:.2f}; confirm to run",
                             "needs_confirm": True, "estimate_usd": est["base_usd"]}, 409)
    with closing(db.connect()) as conn:
        job_id = db.create_job(conn, user, body.kind, answers, spec, est,
                               confirmed_by=user if body.confirm else None, layout=layout)
    return {"id": job_id}


@app.get("/api/jobs")
def list_jobs(limit: int = 20, user: str = Depends(current_user)):
    with closing(db.connect()) as conn:
        return {"jobs": [public_job(j) for j in db.list_jobs(conn, user, max(1, min(limit, MAX_JOBS)))]}


@app.get("/api/files/{path:path}")
def get_file(path: str):
    p = servable(path)
    if p is None:  # one answer for every refusal: never says whether a file exists
        return JSONResponse({"error": "image not found"}, 404)
    return FileResponse(p, media_type=IMAGE_TYPES[p.suffix.lower()],
                        headers={"X-Content-Type-Options": "nosniff"})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    with closing(db.connect()) as conn:
        job = db.get_job(conn, job_id)
    return public_job(job) if job is not None else JSONResponse({"error": f"job {job_id} not found"}, 404)


@app.get("/api/guide/{page:path}")
def get_guide(page: str):
    if page not in guide.PAGES:  # fixed set: no path from the URL ever reaches the disk
        return JSONResponse({"error": f"guide page {page!r} not found"}, 404)
    return PlainTextResponse(guide.render(page), media_type="text/markdown; charset=utf-8")
