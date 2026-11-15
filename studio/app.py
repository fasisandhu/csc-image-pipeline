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
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool
from starlette.datastructures import Headers

from pipeline import compile as compiler
from pipeline import finish, ledger, qa
from pipeline.compile import ROOT, WORD_RANGE, CompileError, compile_spec, load_json
from pipeline.providers import ProviderError
from pipeline.specs import build_spec, find_anchor, load_recipes, longest_prompt_words

from . import db, guide, library
from .brain import BrainError, get_brain
from .identity import current_user
from .notes import plain_warnings
from .questions import missing, resolve, valid_value
from .tasks import draft_rule, read_brief, rule_scopes, rule_words
from .worker import routes_for

STATIC_DIR = Path(__file__).resolve().parent / "static"
# The only files the browser may fetch: images from these folders, and web exports and approval PDFs from out/export.
IMAGE_DIRS = (ROOT / "out" / "drafts", ROOT / "out" / "finals", ROOT / "out" / "reject")
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
EXPORT_DIR = ROOT / "out" / "export"
EXPORT_TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".pdf": "application/pdf"}
DOWNLOADS = {".pdf"}  # served as an attachment, never shown inside the page
MAX_JOBS = 100  # most jobs one list request returns
NOTE_MAX = 1000  # characters in a verdict's note
ANCHOR_FIRST = ("This colour has no approved reference packshot yet. "
                "Make it first, then this shot can use it.")
PICK_A_SKETCH = "pick a sketch from this shot"
NO_SKETCH = "Reference packshots are made without a sketch, so the garment comes only from our catalogue."
BRAIN_BUSY = "Our AI helpers are busy right now; pick the options below."
SERVICE_DOWN = "The image service is not available right now; try again in a moment."
# review, rules and library: the browser shows these words as they are
ALREADY_DECIDED = "already decided"
STILL_RUNNING = "The checks or a fix are still running on this image; wait for them to finish."
NOT_CHECKED_YET = "This image has not been checked yet; run the checks first."
NOTE_NEEDED = "This image needs a person: add a note saying why it is fine."
REJECT_NOTE = "A rejection needs a note: say what is wrong."
NO_RECORD = "This image has no generation record, so it cannot be decided here."
FIX_RUNNING = "a fix is already running"
CHECKS_RUNNING = "checks are already running"
REPAIR_LIMIT = "the repair limit for this image is reached; ask for a new image instead"
NOT_A_CHANGE = "That change is not one this image can ask for."
ALREADY_CHECKED = "This image already passed its checks."
SET_ASIDE = "The checks set this image aside; ask for a change or reject it."
ANCHOR_EXISTS = "a reference packshot already exists for this colour"
FILE_MISSING = "The image file is missing, so nothing was saved."
NOT_SAVED = "The decision could not be saved; nothing changed. Try again."
APPROVE_FIRST = "Approve this image first: nothing is exported without approval."
RULE_WORDS = "A rule needs 1 to 40 words."
RULE_SCOPE = "Pick where the rule applies."
RULE_DONE = {"active": "This rule is already switched on.", "dismissed": "This rule was dismissed."}
RULE_TOO_LONG = "This rule would make some prompts too long; keep it to {n} words or fewer."
IMAGE_GONE = "image not found"  # the files route's words: the image or its record is missing
CROSS_SITE = "This request came from another site and was refused."
READS = ("GET", "HEAD")  # the only methods a page on another site may use here
FOLDERS = re.compile(r"(?:[\w.:~-]*[/\\])+(?=[\w.~-])")  # the folder part of a path, "/" or "\"
SETTING_NAMES = re.compile(r"\b(?=STUDIO_|[A-Z0-9_]*(?:KEY|TOKEN|SECRET|ACCOUNT_ID))[A-Z][A-Z0-9_]{2,}\b")
log = logging.getLogger(__name__)

app = FastAPI(title="CSC Studio", dependencies=[Depends(current_user)])


def cross_site(headers: Headers) -> bool:
    """Whether the browser says this request came from another site: Sec-Fetch-Site other than same-origin or none
    (typed or bookmarked), or an Origin whose host:port is not this request's Host. A request with neither header
    (the CLI, curl, the tests) is not."""
    site = headers.get("sec-fetch-site")
    if site is not None and site.lower() not in ("same-origin", "none"):
        return True
    origin = headers.get("origin")
    return origin is not None and urlsplit(origin).netloc.lower() != headers.get("host", "").lower()


class SameOriginWrites:
    """Refuses (403) a request that can change something (any method but GET or HEAD) when another site's page sent
    it. A form or fetch from another site needs no preflight when it has no JSON body (POST check, POST dismiss), so
    the JSON content type alone does not guard every route."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in READS and cross_site(Headers(scope=scope)):
            await JSONResponse({"error": CROSS_SITE}, 403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


app.add_middleware(SameOriginWrites)
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


class NoteIn(BaseModel):
    note: str = Field("", max_length=NOTE_MAX)


class AnchorIn(NoteIn):
    replace: StrictBool = False  # an existing reference packshot is replaced only with this set


class ChangeIn(BaseModel):
    code: str = Field(max_length=64)


class RuleIn(BaseModel):
    prompt_text: str = Field(max_length=2000)
    applies_to: str = Field(max_length=64)


class ExportIn(BaseModel):
    pdf: StrictBool = False


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
    """The file at repo-relative `path` if the browser may have it, else None: a plain relative "/" path with no
    "..", that resolves to a file with an image suffix under out/drafts, out/finals or out/reject, or to a web
    export (.webp, .png, .jpg) or approval PDF under out/export."""
    if (not path or path.startswith("/") or any(ch in path for ch in "\\:\x00")
            or ".." in path.split("/")):
        return None
    try:
        p = (ROOT / path).resolve()
        suffix = p.suffix.lower()
        if any(p.is_relative_to(d.resolve()) for d in IMAGE_DIRS):
            allowed = suffix in IMAGE_TYPES
        else:
            allowed = p.is_relative_to(EXPORT_DIR.resolve()) and suffix in EXPORT_TYPES
        return p if allowed and p.is_file() else None
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
    suffix = p.suffix.lower()
    media = IMAGE_TYPES.get(suffix) or EXPORT_TYPES[suffix]
    headers = {"X-Content-Type-Options": "nosniff"}
    if suffix in DOWNLOADS:  # a filename makes it an attachment
        return FileResponse(p, media_type=media, filename=p.name, headers=headers)
    return FileResponse(p, media_type=media, headers=headers)


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


# --- review: verdicts, change requests and checks on a final's latest image ---------------------
def _final(conn, job_id: int) -> dict | None:
    """A finished final job (the only kind with an image to review), else None."""
    job = db.get_job(conn, job_id)
    return job if job and job["kind"] == "final" and job["status"] == "done" else None


def _no_final(job_id: int) -> JSONResponse:
    return JSONResponse({"error": f"no finished image for job {job_id}"}, 404)


def _latest(conn, job: dict) -> str | None:
    return library.latest_image(job, db.steps_for(conn, job["id"]))


def _item(conn, job_id: int) -> dict:
    with ledger.snapshot():
        return library.review_item(db.get_job(conn, job_id), db.steps_for(conn, job_id))


def _refused(conn, job_id: int, running: str = STILL_RUNNING) -> JSONResponse:
    """The 409 for a verdict or request this job's review is not open to, read again now: a person decided, the
    checks or a fix run, or it was never checked."""
    review = db.get_job(conn, job_id)["review"]
    if review in db.VERDICTS:
        msg = ALREADY_DECIDED
    elif review in ("checking", "fixing") or db.busy(conn, job_id):
        msg = running
    else:
        msg = NOT_CHECKED_YET
    return JSONResponse({"error": msg}, 409)


def _recorded(latest: str | None) -> bool:
    """Whether the image is a logged render whose spec exists: what finish.record_verdict needs."""
    entry = ledger.find(latest) if latest else None
    if not entry or not entry.get("shot_id"):
        return False
    try:
        qa.spec_path(entry["shot_id"])
    except FileNotFoundError:
        return False
    return True


def _logo_refused(conn, job: dict, latest: str | None) -> JSONResponse | None:
    """The 409 when the latest image's logo has not cleared its check (library.logo_cleared), else None."""
    with ledger.snapshot():
        if library.logo_cleared(job["spec"], db.steps_for(conn, job["id"]), latest):
            return None
    return JSONResponse({"error": library.LOGO_NOT_CLEARED}, 409)


def _undo(conn, job: dict, state: str) -> None:
    """Put back the review a verdict claimed, when what the verdict writes could not be written."""
    db.set_review(conn, job["id"], job["review"], only_from=(state,), reason=job["review_reason"])


@app.get("/api/reviews")
def list_reviews(user: str = Depends(current_user)):
    with closing(db.connect()) as conn, ledger.snapshot():
        return {"items": [library.review_item(j, db.steps_for(conn, j["id"]))
                          for j in db.done_finals(conn, user, MAX_JOBS)]}


@app.get("/api/reviews/{job_id}")
def get_review(job_id: int):
    with closing(db.connect()) as conn:
        return _item(conn, job_id) if _final(conn, job_id) else _no_final(job_id)


@app.post("/api/reviews/{job_id}/approve")
def approve(job_id: int, body: NoteIn, user: str = Depends(current_user)):
    """From "ready", or from "needs_person" with a note saying why. One verdict per job: a conditional update.
    Never an image whose logo has not cleared its check: no note overrides that (CLAUDE.md non-negotiable)."""
    note = body.note.strip()
    with closing(db.connect()) as conn:
        job = _final(conn, job_id)
        if job is None:
            return _no_final(job_id)
        latest = _latest(conn, job)
        if job["review"] in library.ACTIONABLE and (refused := _logo_refused(conn, job, latest)):
            return refused
        if job["review"] == "needs_person" and not note:
            return JSONResponse({"error": NOTE_NEEDED}, 422)
        if job["review"] in library.ACTIONABLE and not _recorded(latest):
            return JSONResponse({"error": NO_RECORD}, 409)
        if not db.set_review(conn, job_id, "approved", only_from=library.ACTIONABLE if note else ("ready",)):
            return _refused(conn, job_id)
        try:
            finish.record_verdict(ROOT / latest, "approved", note, by=user)
        except (Exception, SystemExit):
            log.exception("approval of job %s not recorded", job_id)
            _undo(conn, job, "approved")
            return JSONResponse({"error": NOT_SAVED}, 500)
        return {"item": _item(conn, job_id)}


def _draft(note: str, user: str, job: dict) -> dict:
    """The brain's draft of a rejection's rule; an empty one when it cannot answer (the person writes it)."""
    try:
        about = f"{library.shot_words(job)} (style id {job['spec']['shot']['garment']['catalogue']})"
        return draft_rule(note, get_brain(user, fast=True), user=user, about=about)
    except (BrainError, ValueError, KeyError) as e:  # e.g. a user setting the brain cannot use
        log.warning("rule draft skipped: %s", type(e).__name__)
        return {"prompt_text": "", "applies_to": "all"}


@app.post("/api/reviews/{job_id}/reject")
def reject(job_id: int, body: NoteIn, user: str = Depends(current_user)):
    """Needs a note. The note becomes a pending prompt rule (finish.record_verdict), and the brain drafts its
    sentence and scope for a person to check; it stays pending until someone switches it on."""
    note = body.note.strip()
    if not note:
        return JSONResponse({"error": REJECT_NOTE}, 422)
    with closing(db.connect()) as conn:
        job = _final(conn, job_id)
        if job is None:
            return _no_final(job_id)
        latest = _latest(conn, job)
        if job["review"] in library.ACTIONABLE and not _recorded(latest):
            return JSONResponse({"error": NO_RECORD}, 409)
        if not db.set_review(conn, job_id, "rejected", only_from=library.ACTIONABLE):
            return _refused(conn, job_id)
        try:
            with library.RULES_LOCK:
                index = finish.record_verdict(ROOT / latest, "rejected", note, by=user)
        except (Exception, SystemExit):
            log.exception("rejection of job %s not recorded", job_id)
            _undo(conn, job, "rejected")
            return JSONResponse({"error": NOT_SAVED}, 500)
    rule = _drafted_rule(index, note, user, job) if index is not None else None
    with closing(db.connect()) as conn:
        return {"item": _item(conn, job_id), "rule": library.public_rule(index, rule) if rule else None}


def _drafted_rule(index: int, note: str, user: str, job: dict) -> dict | None:
    """The rejection's pending rule with the brain's draft on it. The rejection is already recorded, so a draft
    that cannot be made or stored only leaves the rule as it is, for the person to write."""
    try:
        return library.store_draft(index, note, _draft(note, user, job))
    except Exception:
        log.exception("rule draft for rule %s not stored", index)
    try:
        rules = compiler.load_rules()["rules"]
        return rules[index] if 0 <= index < len(rules) else None
    except (OSError, ValueError):
        return None


@app.post("/api/reviews/{job_id}/change")
def change(job_id: int, body: ChangeIn):
    """A one-change repair of the latest image, within its repair limit; one fix at a time."""
    with closing(db.connect()) as conn:
        job = _final(conn, job_id)
        if job is None:
            return _no_final(job_id)
        if body.code not in library.change_codes(job["spec"]):
            return JSONResponse({"error": NOT_A_CHANGE}, 422)
        if job["review"] not in library.ACTIONABLE or db.busy(conn, job_id):
            return _refused(conn, job_id, running=FIX_RUNNING)
        latest = _latest(conn, job)
        with ledger.snapshot():
            entry = ledger.find(latest) if latest else None
            left = library.limits_left(entry, job["spec"]["shot"]["shot_id"])["repairs"]
        if entry is None or not _recorded(latest):
            return JSONResponse({"error": NO_RECORD}, 409)
        if left <= 0:
            return JSONResponse({"error": REPAIR_LIMIT}, 409)
        step = db.queue_step(conn, job_id, "repair", latest, body.code, library.ACTIONABLE, "fixing")
        if step is None:
            return _refused(conn, job_id, running=FIX_RUNNING)
        return {"item": _item(conn, job_id), "step_id": step}


@app.post("/api/reviews/{job_id}/check")
def recheck(job_id: int):
    """Run the checks on a final never checked (from before the review chain) or one that needs a person. No new
    image is made; the checks' own small calls are logged as usual."""
    with closing(db.connect()) as conn:
        job = _final(conn, job_id)
        if job is None:
            return _no_final(job_id)
        review = job["review"]
        if review in db.VERDICTS:
            return JSONResponse({"error": ALREADY_DECIDED}, 409)
        if review in ("checking", "fixing") or db.busy(conn, job_id):
            return JSONResponse({"error": CHECKS_RUNNING}, 409)
        if review == "ready":
            return JSONResponse({"error": ALREADY_CHECKED}, 409)
        latest = _latest(conn, job)
        if not latest or not (ROOT / latest).is_file() or not library.in_finals(latest):
            return JSONResponse({"error": SET_ASIDE}, 409)
        entry = ledger.find(latest)
        if entry is None or entry.get("shot_id") != job["spec"]["shot"]["shot_id"]:
            return JSONResponse({"error": NO_RECORD}, 409)
        step = db.queue_step(conn, job_id, "check", latest, None, (None, "needs_person"), "checking")
        if step is None:
            again = db.get_job(conn, job_id)["review"]
            return JSONResponse({"error": ALREADY_DECIDED if again in db.VERDICTS else CHECKS_RUNNING}, 409)
        return {"item": _item(conn, job_id), "step_id": step}


@app.post("/api/reviews/{job_id}/anchor")
def approve_anchor(job_id: int, body: AnchorIn, user: str = Depends(current_user)):
    """Approve the latest image as its colour's reference packshot: only a plain packshot-front final made without a
    sketch, still in out/finals; from "needs_person" only with a note. Written as
    refs/anchors/<catalogue>_<colour_id>_<gsm>_front.png; an existing one needs replace. The file and its two ledger
    records (approved, anchor_approved) are one step: if any fails, the earlier file and the review are put back."""
    note = body.note.strip()
    with closing(db.connect()) as conn:
        job = _final(conn, job_id)
        if job is None:
            return _no_final(job_id)
        latest = _latest(conn, job)
        with ledger.snapshot():
            refusal = library.anchor_refusal(job, latest, ledger.find(latest) if latest else None)
        if refusal:
            return JSONResponse({"error": refusal}, 409)
        if refused := _logo_refused(conn, job, latest):  # never decorated today (NOT_PLAIN): kept as a second lock
            return refused
        if job["review"] == "needs_person" and not note:
            return JSONResponse({"error": NOTE_NEEDED}, 422)
        if job["review"] not in library.ACTIONABLE:
            return _refused(conn, job_id)
        if not _recorded(latest):
            return JSONResponse({"error": NO_RECORD}, 409)
        if not (ROOT / latest).is_file():
            return JSONResponse({"error": FILE_MISSING}, 409)
        name = library.anchor_name(job["spec"])
        exists = {"error": ANCHOR_EXISTS, "anchor_exists": True}
        if (library.ANCHORS_DIR / name).exists() and not body.replace:
            return JSONResponse(exists, 409)
        if not db.set_review(conn, job_id, "anchor", only_from=library.ACTIONABLE if note else ("ready",)):
            return _refused(conn, job_id)

        def record(replaced: bool) -> None:
            # approved first: if the second record fails, what is left is a person's approval of this image, never
            # an anchor_approved event that the chain's logo placement would follow (worker.anchor_source)
            finish.record_verdict(ROOT / latest, "approved", note, by=user)
            ledger.record({"event": "anchor_approved", "file": latest, "shot_id": job["spec"]["shot"]["shot_id"],
                           "anchor": f"{library.ANCHORS_REL}/{name}", "by": user, "replaced": replaced})
        try:
            replaced = library.publish_anchor(ROOT / latest, name, body.replace, record)
        except (Exception, SystemExit):
            log.exception("reference packshot for job %s not saved", job_id)
            _undo(conn, job, "anchor")
            return JSONResponse({"error": NOT_SAVED}, 500)
        if replaced is None:  # another request wrote this colour's reference packshot meanwhile
            _undo(conn, job, "anchor")
            return JSONResponse(exists, 409)
        return {"item": _item(conn, job_id), "anchor": name}


# --- prompt rules ------------------------------------------------------------------------------
@app.get("/api/rules")
def list_rules():
    rules = compiler.load_rules()["rules"]
    return {"rules": [library.public_rule(i, r) for i, r in enumerate(rules)], "scopes": library.scope_options()}


def _settle_rule(index: int, status: str, user: str, check=None, **fields):
    """Move pending rule `index` to `status` ("active" or "dismissed"), recording who did it; 404 or 409 otherwise.
    check(): a refusal (a response) or None, run under the lock just before the change."""
    with library.RULES_LOCK:
        doc = compiler.load_rules()
        rules = doc["rules"]
        if not 0 <= index < len(rules):
            return JSONResponse({"error": f"rule {index} not found"}, 404)
        rule = rules[index]
        if rule.get("status") != "pending":
            return JSONResponse({"error": RULE_DONE.get(rule.get("status"), "This rule is not pending.")}, 409)
        refused = check() if check else None
        if refused is not None:
            return refused
        by = "activated_by" if status == "active" else "dismissed_by"
        rule.update(status=status, **fields, **{by: user})
        compiler.save_rules(doc)
    return {"rule": library.public_rule(index, rule)}


@app.post("/api/rules/{index}/activate")
def activate_rule(index: int, body: RuleIn, user: str = Depends(current_user)):
    """Switch a pending rule on with the person's own words (1 to 40 words) and scope: from then on it is part of
    every compiled prompt in that scope. Refused when the longest prompt in that scope would pass CLAUDE.md's 220
    words."""
    text = rule_words(body.prompt_text)
    if text is None:
        return JSONResponse({"error": RULE_WORDS}, 422)
    if body.applies_to not in rule_scopes():
        return JSONResponse({"error": RULE_SCOPE}, 422)

    def fits():  # the longest prompt in the rule's scope, with the rules already on, plus this one
        room = WORD_RANGE[1] - longest_prompt_words(body.applies_to)
        if len(text.split()) > room:
            return JSONResponse({"error": RULE_TOO_LONG.format(n=max(0, room))}, 409)
        return None
    return _settle_rule(index, "active", user, check=fits, prompt_text=text, applies_to=body.applies_to)


@app.post("/api/rules/{index}/dismiss")
def dismiss_rule(index: int, user: str = Depends(current_user)):
    return _settle_rule(index, "dismissed", user)


# --- library -----------------------------------------------------------------------------------
@app.get("/api/library")
def get_library(style: str | None = None, colour: str | None = None, technique: str | None = None,
                placement: str | None = None, model: str | None = None, kind: str | None = None):
    """Approved images (their latest image has an `approved` ledger event) from every user's finals, newest
    approval first, with the filter values present among them."""
    with closing(db.connect()) as conn, ledger.snapshot():
        items = [i for j in db.done_finals(conn) if (i := library.library_item(j, db.steps_for(conn, j["id"])))]
    want = {k: v for k, v in (("style", style), ("colour", colour), ("technique", technique),
                              ("placement", placement), ("model", model), ("kind", kind)) if v}
    shown = [i for i in items if all(i.get(k) == v for k, v in want.items())]
    shown.sort(key=lambda i: (i["approved_at"] or "", i["id"]), reverse=True)
    return {"items": shown, "facets": library.facets(items)}


@app.post("/api/library/{job_id}/export")
def export_image(job_id: int, body: ExportIn):
    """Web sizes (WebP 1600/800/400, up to the image's width), alt text and caption; with pdf, the approval sheet.
    Only an approved image whose logo cleared its check (library.logo_cleared), however it was approved."""
    with closing(db.connect()) as conn:
        job = _final(conn, job_id)
        if job is None:
            return _no_final(job_id)
        steps = db.steps_for(conn, job_id)
    latest = library.latest_image(job, steps)
    if not latest or not (ROOT / latest).is_file() or not _recorded(latest):
        return JSONResponse({"error": IMAGE_GONE}, 404)
    with ledger.snapshot():
        approved = library.approval(latest)
        cleared = library.logo_cleared(job["spec"], steps, latest)
    if approved is None:
        return JSONResponse({"error": APPROVE_FIRST}, 409)
    if not cleared:
        return JSONResponse({"error": library.LOGO_NOT_CLEARED}, 409)
    try:
        finish.export(ROOT / latest, body.pdf)
    except SystemExit:  # its own approval check (checked just above): never let a CLI exit reach the server
        return JSONResponse({"error": APPROVE_FIRST}, 409)
    with ledger.snapshot():
        return library.export_record(latest)
