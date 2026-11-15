// CSC Studio page: one Alpine store, organised by concern (storage, api, builder rules, then the
// store: budget, tabs, builder questions, answers, preview, fill from my words, the summary, and
// the jobs: sketches, approve & create, the job panel and recent images).
// Only UI conveniences go in localStorage (tour seen, draft of the answers), always in try/catch.
(function () {
  const BUDGET_REFRESH_MS = 60000;
  const QUESTIONS_DEBOUNCE_MS = 150;
  const PREVIEW_DEBOUNCE_MS = 300;
  const BRIEF_TIMEOUT_MS = 30000;
  const MAX_BRIEF = 500;
  const TABS = [
    { id: 'create', label: 'Create image' },
    { id: 'clients', label: 'Clients', later: true },
    { id: 'review', label: 'Review' },
    { id: 'library', label: 'Library' },
    { id: 'brand', label: 'Brand kit', later: true },
    { id: 'ads', label: 'Ads', dim: true },
    { id: 'social', label: 'Social', dim: true },
    { id: 'seo', label: 'SEO', dim: true },
  ];

  // --- storage (UI conveniences only) ---------------------------------------------------------
  const storage = {
    get(key, fallback) {
      try { const v = window.localStorage.getItem('csc.' + key); return v === null ? fallback : JSON.parse(v); }
      catch (e) { return fallback; }
    },
    set(key, value) {
      try { window.localStorage.setItem('csc.' + key, JSON.stringify(value)); } catch (e) { /* ignore */ }
    },
  };

  // --- api ------------------------------------------------------------------------------------
  async function api(path, options) {
    const res = await fetch(path, options);
    if (!res.ok) throw new Error('The studio could not answer (' + res.status + '). Try again in a moment.');
    return res.json();
  }

  function post(body) {
    return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  }

  // --- builder rules: UI copy and drawing choices only; every fact comes from /api/questions ----
  const NEW_REASONS = {  // why a section just appeared, keyed by section id
    cast: 'because you picked a shot with a model',
    scene: 'because you picked a shot with a model',
    design: 'because you picked a design',
    technique: 'because you picked a design',
    placement: 'because you picked a design',
    size_cm: 'because you picked a design',
  };
  const TILE_VIEWS = { catalogue: 'style', cast: 'cast', design: 'design' };  // other choices: segmented
  const HOODIES = { 'pullover-classic': '#h-classic', 'pullover-oversized': '#h-over', zip: '#h-zip' };
  const NUMERIC = new Set(['gsm', 'size_cm']);  // option ids are text; the answer is a number
  const HIDE_SUB = new Set(['recipe']);  // a shot's sub ("on-model") repeats its label
  const HEX_RE = /^#[0-9A-Fa-f]{6}$/;
  const SIZE_KEYS = new Set(['ArrowRight', 'ArrowUp', 'ArrowLeft', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown']);
  const MAX_COLOUR_NAME = 40;
  const NO_COLOUR = '#8d8d94';  // hoodie drawings before a colour is picked
  const NOTES = {
    options: "Couldn't load the options; check the studio is running.",
    preview: "Couldn't work out the cost for these choices; check the studio is running.",
    briefSlow: 'That took too long; you can still pick below',
    briefBusy: 'Our AI helpers are busy right now; pick the options below.',
    briefFailed: "Couldn't read that; check the studio is running, or pick the options below.",
    briefNothing: "Didn't pick up anything new from that; pick the options below.",
  };
  const ROW_LABELS = {  // short names for the summary rows, keyed by section id
    purpose: 'For', catalogue: 'Style', colour: 'Colour', gsm: 'Weight', recipe: 'Shot', cast: 'Model',
    scene: 'Scene', decoration: 'Decoration', design: 'Design', technique: 'Technique', placement: 'Placement',
    size_cm: 'Size',
  };
  // picked-up chips read as plain words: these labels are lower-cased ("olive", "screen print")
  const CHIP_LOWER = new Set(['purpose', 'catalogue', 'colour', 'recipe', 'scene', 'decoration', 'technique', 'placement']);
  const PROVIDERS = { claude: 'Claude', gemini: 'Gemini' };
  // jobs: the server makes them; the page starts them, watches them and shows their images
  const POLL_MS = 2000;
  const POLL_FAILS = 3;  // status checks in a row that fail before the page stops checking
  const JOB_LIST = 20;  // jobs fetched on load: any queued or running among them is watched again
  const RECENT = 6;  // entries in "Recent images"
  const ACTIVE = new Set(['queued', 'running']);
  const STATUS_WORDS = { queued: 'Waiting', running: 'Making…', done: 'Done', failed: 'Failed', interrupted: 'Stopped' };
  const JOB_NOTES = {
    queued: 'Waiting for the studio to start it…',
    drafts: 'Making 3 free sketches… usually under a minute.',
    final: 'Creating your image… this can take a minute.',
    anchor: 'Making the reference packshot… this can take a minute.',
    pick: 'Click the layout you like; Approve & create builds on it.',
    // a plain front packshot (it can become its colour's reference): the server says so in the preview
    noSketch: 'Made without a sketch: a plain front packshot can become the reference for its colour, so its garment comes only from our catalogue.',
    lookOnly: 'Reference packshots are made without a sketch; click one to see it larger.',
    otherShot: 'These sketches are for other choices than the ones above, so they cannot be picked. Make new sketches for this shot.',
    failed: 'Nothing was retried. You can try again with the buttons above.',
    noError: 'It stopped without saying why.',
    interrupted: 'Stopped when the studio restarted; nothing was retried.',
    review: 'Its checks, fixes and approval are in the Review tab.',
    anchorDone: 'Your shot can use this reference packshot once it is approved: in the Review tab, press “Approve as reference packshot”, then come back and create your shot.',
    unreachable: "Couldn't reach the studio; check it is running, then try again.",
    refused: "The studio couldn't start this. Try again in a moment.",
    list: "Couldn't load your recent images; check the studio is running.",
    lost: 'Lost touch with the studio while checking on your images. Check it is running, then check again.',
    unchecked: 'Not being checked: lost touch with the studio (see Recent images).',
    changed: 'Your choices changed — check the cost again.',
  };
  // the guide: the server's pages (studio/guide.py PAGES), in its order; the text comes from /api/guide/<id>
  const GUIDE_PAGES = [
    { id: 'start-here', title: 'Start here' },
    { id: 'make-a-website-image', title: 'Make a website image' },
    { id: 'review-and-approve', title: 'Review and approve' },
    { id: 'read-the-checks', title: 'Read the checks' },
    { id: 'rules', title: 'Rules' },
  ];
  const GUIDE_LATER = 'Coming later: client orders, costs and budget, when something goes wrong.';
  const GUIDE_ERROR = "Couldn't load this page; check the studio is running, then try again.";
  // the first-visit tour: one pointer per part of the builder, then the Review tab; a step whose part is hidden is
  // skipped, and so is a step on a tab that is not live (tab: its TABS id)
  const TOUR = [
    { anchor: '#idea', title: 'Start with your idea',
      text: 'Type what you want in your own words and press Fill from my words. Or skip this and pick below.' },
    { anchor: '#builder', title: 'Pick the rest',
      text: 'Each section is one choice. Answered ones fold to one line; press Change to edit. A greyed option says why it can’t be used.' },
    { anchor: '#summary', title: 'Your image',
      text: 'Everything you chose, with the estimated cost. Open “Show the full prompt” to see what the pipeline wrote.' },
    { anchor: '#btn-sketch', title: 'Free sketches (optional)',
      text: 'Quick rough layouts at no cost. Click one to build on it. Sketches never go on the website.' },
    { anchor: '#btn-create', title: 'Approve & create',
      text: 'Makes the real image and shows it here with its caption. Big spends ask you first; the Guide has the exact rules.' },
    { anchor: '#tab-review', tab: 'review', title: 'Review tab',
      text: 'Each new image is checked here and fixed within its limits. Approve it, or reject it with a note; approved images go to the Library to export.' },
  ];
  const TOUR_GAP = 12;  // px between the tour card and what it points at, and from the window edge
  const TOUR_PAD = 6;  // px of the highlight ring around what it points at
  // review: each finished final, its checks and fixes, and a person's verdict. The server says what an item
  // allows (item.actions) and gives every word about the image in plain language; the page only draws it.
  const REVIEW_POLL_MS = 3000;  // while any item is being checked or fixed
  const REVIEW_WORDS = {
    checking: 'Checking…', fixing: 'Fixing…', ready: 'Ready for your review', needs_person: 'Needs a person',
    approved: 'Approved', anchor: 'Reference packshot', rejected: 'Rejected',
  };
  const REVIEW_CLASS = {
    checking: 's-running', fixing: 's-running', ready: 's-ready', needs_person: 's-person',
    approved: 's-done', anchor: 's-done', rejected: 's-failed',
  };
  const REVIEW_GROUPS = [  // the list's sections, in this order; an item never checked waits for a person
    { id: 'waiting', title: 'Waiting for you', states: [null, 'ready', 'needs_person'] },
    { id: 'working', title: 'Being checked or fixed', states: ['checking', 'fixing'] },
    { id: 'decided', title: 'Decided', states: ['approved', 'anchor', 'rejected'] },
  ];
  const BUSY_REVIEW = new Set(['checking', 'fixing']);
  const BUSY_STEP = new Set(['queued', 'running']);
  const OPEN_ACTIONS = ['approve', 'anchor', 'change', 'reject'];  // any of these: the item awaits a person
  // the chain's images after the first, named by the step that made each (step kinds are never shown)
  const FIX_SHORT = { repair: 'Repaired', finish: 'Colour fixed', regenerate: 'New image', composite: 'Logo placed' };
  const STEP_STATUS = { queued: 'waiting', running: 'running…', done: 'done', failed: 'stopped, not retried' };
  const CHECK_MARKS = { ok: '✓', fix: '↻', bad: '✗', look: '?' };
  const CHECK_SR = { ok: 'Passed: ', fix: '', bad: 'Problem: ', look: 'Please look: ' };
  const NOT_CHECKED = 'Not checked — please look';
  const REVIEW_NOTES = {
    empty: 'No finished images yet. Images you create appear here with their checks, for your approval.',
    list: "Couldn't load the images to review; check the studio is running.",
    lost: 'Lost touch with the studio while checking on the review. Check it is running, then check again.',
    unreachable: "Couldn't reach the studio; check it is running, then try again.",
    refused: "The studio couldn't do that. Try again in a moment.",
    notChecked: 'The checks have not run on this image yet.',
    checking: 'The checks are running…',
    approved: 'Approved.',
    anchor: 'Saved as the reference packshot for this colour.',
    rejected: 'Rejected. Your note is saved as a draft rule for future images (see Rules from rejection notes, below); it stays off until someone switches it on.',
    checksStarted: 'The checks are running; this page updates by itself.',
    replace: 'A reference packshot already exists for this colour. Replace it?',
    builder: 'These are that image’s choices. Change what you like, then make it again.',
    anchorHint: 'Later shots in this colour copy it as the exact garment.',
    checkHint: 'No new image is made; the checks make a few small AI calls.',
    decided: 'This image is decided. Change your choices to make a new one.',
    running: 'A check or fix is running on this image; wait for it to finish.',
    noRepairs: 'No repairs are left on this image. Change your choices to make a new one.',
    noChanges: 'No fixes can be asked for on this image here. Change your choices to make a new one.',
  };
  // prompt rules: each rejection note is a pending rule with the AI's draft sentence; a person edits it, picks where
  // it applies and switches it on (or dismisses it). Only a click on "Switch on" ever turns one on.
  const RULE_WORDS_MAX = 40;  // studio.tasks.RULE_WORDS_MAX: the server refuses more
  const RULE_NOTES = {
    list: "Couldn't load the rules; check the studio is running.",
    unreachable: "Couldn't reach the studio; check it is running, then try again.",
    refused: "The studio couldn't do that. Try again in a moment.",
    noDraft: 'No draft was made for this note; write the rule yourself.',
    on: 'Switched on. From now on it is part of every prompt for ',
    dismissed: 'Dismissed. This rule will not be used.',
  };
  // library: approved images with their record, filters built from the items present, and the web export
  const FILTERS = [
    { key: 'style', label: 'Style' }, { key: 'colour', label: 'Colour' }, { key: 'technique', label: 'Technique' },
    { key: 'placement', label: 'Placement' }, { key: 'model', label: 'Model' }, { key: 'kind', label: 'Packshot / on model' },
  ];
  const KIND_WORDS = { packshot: 'Packshot', on_model: 'On model' };
  const PURPOSE_WORDS = { website: 'Website sample', client: 'Client mockup' };
  const COPY_MS = 4000;  // how long "Copied" stays on a copy button
  const LIBRARY_NOTES = {
    empty: 'No approved images yet — approve one in Review.',
    none: 'No approved images match these filters.',
    list: "Couldn't load the library; check the studio is running.",
    unreachable: "Couldn't reach the studio; check it is running, then try again.",
    refused: "The studio couldn't export this. Try again in a moment.",
    gone: 'The image file is missing, so it cannot be exported.',
    exported: 'Exported. The web files, alt text and caption are below.',
    pdf: 'The approval PDF is ready below, with the web files.',
    noPrompt: 'The prompt could not be rebuilt for this image.',
    noFiles: 'No web sizes were made: the image is narrower than 400 px.',
    left: 'The image you had open is no longer approved (rejected since, or its file is gone), so it left the Library.',
  };

  function plainObject(v) { return v && typeof v === 'object' && !Array.isArray(v) ? v : {}; }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  function stable(v) {  // JSON with object keys sorted, so key order never makes two answers differ
    if (Array.isArray(v)) return '[' + v.map(stable).join(',') + ']';
    if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + stable(v[k])).join(',') + '}';
    return String(JSON.stringify(v));
  }
  function sameAnswers(a, b) { return stable(plainObject(a)) === stable(plainObject(b)); }
  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function money(n) { return '$' + Number(n).toFixed(2); }
  function capital(t) { return t ? t.charAt(0).toUpperCase() + t.slice(1) : t; }
  function reducedMotion() {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
  }
  function scrollMode() { return reducedMotion() ? 'auto' : 'smooth'; }
  // the builder's sections in the page: found by their heading id, so a section's DOM is never cached
  function sectionEl(id) { const h = document.getElementById('sec-' + id); return h ? h.closest('section') : null; }
  function shown(el) { return !!el && el.getClientRects().length > 0; }  // display:none has no boxes
  function firstShown(root, selector) { return root ? Array.from(root.querySelectorAll(selector)).find(shown) || null : null; }
  // the section in use keeps its place on screen while a section above it folds or opens
  function holdPlace(id) {
    const el = sectionEl(id);
    if (!el) return;
    const top = el.getBoundingClientRect().top;
    window.Alpine.nextTick(() => {
      const now = sectionEl(id);
      const shift = now ? now.getBoundingClientRect().top - top : 0;
      if (Math.abs(shift) >= 1) window.scrollBy({ top: shift, left: 0, behavior: 'instant' });
    });
  }
  // an output image the server may serve (out/drafts, out/finals): each path segment URL-encoded
  function fileUrl(path) { return '/api/files/' + String(path).split('/').map(encodeURIComponent).join('/'); }
  function jobFiles(j) {
    const files = j && j.result && Array.isArray(j.result.files) ? j.result.files : [];
    return files.filter(f => typeof f === 'string' && f);
  }
  function clockText(iso) {  // a job's time: hh:mm today, else the day and month
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const today = d.toDateString() === new Date().toDateString();
    return today ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString([], { day: 'numeric', month: 'short' });
  }

  // --- guide Markdown: drawn as DOM nodes from marked's tokens -----------------------------------
  // No HTML string from the Markdown ever reaches the page: every piece of text becomes a text node,
  // tags come only from the fixed lists below, raw HTML in the Markdown shows as plain text, images
  // show their alt text, and only an http(s) link becomes a link (in a new tab).
  const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" };
  // marked escapes inline text and code for its own HTML output; text nodes need the plain characters
  function plain(t) { return String(t === undefined || t === null ? '' : t).replace(/&(amp|lt|gt|quot|#39);/g, (m, k) => ENTITIES[k]); }
  function textNode(t) { return document.createTextNode(t); }
  function make(tag, kids) { const el = document.createElement(tag); el.append(...kids); return el; }
  const INLINE_TAGS = { strong: 'strong', em: 'em', del: 'del' };
  function inlineNodes(tokens) {
    const out = [];
    for (const t of Array.isArray(tokens) ? tokens : []) {
      if ((t.type === 'text' || t.type === 'escape') && Array.isArray(t.tokens)) out.push(...inlineNodes(t.tokens));
      else if (t.type === 'text' || t.type === 'escape') out.push(textNode(plain(t.text)));
      else if (INLINE_TAGS[t.type]) out.push(make(INLINE_TAGS[t.type], inlineNodes(t.tokens)));
      else if (t.type === 'codespan') out.push(make('code', [textNode(plain(t.text))]));
      else if (t.type === 'br') out.push(document.createElement('br'));
      else if (t.type === 'link') out.push(linkNode(t));
      else if (t.type === 'image') out.push(textNode(plain(t.text)));
      else out.push(textNode(String(t.raw || '')));  // raw HTML and anything unknown: its source, as text
    }
    return out;
  }
  function linkNode(t) {
    const kids = inlineNodes(t.tokens);
    if (!/^https?:\/\//i.test(String(t.href || ''))) return make('span', kids);  // the guide has no inner links
    const a = make('a', kids);
    a.href = t.href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    return a;
  }
  function blockNodes(tokens) {
    const out = [];
    for (const t of Array.isArray(tokens) ? tokens : []) {
      if (t.type === 'space') continue;
      if (t.type === 'heading') out.push(make('h' + Math.min(6, Math.max(2, Number(t.depth) + 1 || 2)), inlineNodes(t.tokens)));
      else if (t.type === 'paragraph') out.push(make('p', inlineNodes(t.tokens)));
      else if (t.type === 'text') out.push(...inlineNodes([t]));  // the text of a tight list item
      else if (t.type === 'list') {
        const list = make(t.ordered ? 'ol' : 'ul', (t.items || []).map(it => make('li', blockNodes(it.tokens))));
        if (t.ordered && Number(t.start) > 1) list.start = Number(t.start);
        out.push(list);
      } else if (t.type === 'blockquote') out.push(make('blockquote', blockNodes(t.tokens)));
      else if (t.type === 'code') out.push(make('pre', [make('code', [textNode(String(t.text || ''))])]));
      else if (t.type === 'hr') out.push(document.createElement('hr'));
      else out.push(make('p', [textNode(String(t.raw || ''))]));  // raw HTML blocks and anything unknown: as text
    }
    return out;
  }
  function markdownNodes(md) {  // marked missing (vendor file not loaded): the Markdown as plain text
    if (!window.marked || typeof window.marked.lexer !== 'function') return [make('pre', [textNode(String(md))])];
    return blockNodes(window.marked.lexer(String(md)));
  }

  // timers and request numbers: a reply is used only if no newer request was made since.
  // questionsOpen / previewOpen: the number of the newest request still in flight (0 if none).
  let questionsTimer = null, questionsSeq = 0, questionsOpen = 0;
  let previewTimer = null, previewSeq = 0, previewOpen = 0;
  let wordsReveal = false;  // the next questions reply shows sections opened by "Fill from my words"
  const edits = {};  // section id -> how often the person set it: a brief reply never overrides a newer edit
  // folding: touchSeq counts the person's acts in the builder; briefJump is touchSeq when words were
  // filled in, so the next questions reply moves to the first gap only if the person did nothing since
  let touchSeq = 0, briefJump = null;
  let pollTimer = null, pollBusy = false, pollFails = 0;  // one status check of the active jobs at a time
  // guide: page texts fetched while the guide is open (cleared on each open, so numbers stay current);
  // guideSeq: only the newest page asked for is drawn
  const guideCache = new Map();
  let guideSeq = 0, guidePressOutside = false;  // guidePressOutside: the last press began on the backdrop
  // tour: where the keyboard goes back to when it ends, and the listeners that keep the card in place
  let tourReturn = null, tourFrame = 0, tourResize = null;
  // review: reviewSeq numbers list requests (only the newest reply is used); reviewEpoch counts items replaced
  // by an action's reply, and reviewActed holds each item's last one, so a list sent before an action never
  // puts back that item's older state
  let reviewTimer = null, reviewSeq = 0, reviewEpoch = 0, reviewFails = 0;
  const reviewActed = new Map();
  // rules and library: the same sequencing. A list reply is used only if no newer list was asked for, and a list sent
  // before an action's reply never puts back that rule's (or that export's) older state
  let rulesSeq = 0, rulesEpoch = 0, librarySeq = 0, libraryEpoch = 0, copyTimer = null;
  const rulesActed = new Map(), libraryActed = new Map();

  function busyReview(it) {
    return BUSY_REVIEW.has(it.review) || (Array.isArray(it.steps) && it.steps.some(s => BUSY_STEP.has(s.status)));
  }
  function smallMoney(n) { const v = Number(n) || 0; return v > 0 && v < 0.005 ? 'under $0.01' : money(v); }
  function fileName(p) { return String(p || '').split('/').pop(); }
  function wordCount(t) { return String(t || '').split(/\s+/).filter(Boolean).length; }
  function dateTime(iso) {  // an approval's day and time, e.g. "15 Nov, 14:05"
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleDateString([], { day: 'numeric', month: 'short' }) + ', '
      + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  function selectText(el) {  // the copy fallback: the text is selected, for Ctrl+C
    const sel = el && window.getSelection ? window.getSelection() : null;
    if (!sel) return;
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
  }
  // The finished fix that made each of the chain's images (studio.library.chain_images skips a step with no output
  // and an output already listed, so position alone can shift): matched by its served output path, else by its file
  // name (a render the checks moved to out/reject keeps its name, but not its path). The first image, and one no
  // step names, get none. Only when no fix step says what it made is their order used.
  function chainFixes(it) {
    const images = Array.isArray(it.images) ? it.images : [];
    const fixes = (Array.isArray(it.steps) ? it.steps : []).filter(s => s.kind !== 'check' && s.status === 'done');
    const named = fixes.some(s => s.output || s.file);
    const used = new Set();
    return images.map((p, k) => {
      if (k === 0) return null;
      if (!named) return fixes[k - 1] || null;
      if (!p) return null;
      const s = fixes.find(f => !used.has(f.id) && f.output === p)
        || fixes.find(f => !used.has(f.id) && f.file && f.file === fileName(p));
      if (s) used.add(s.id);
      return s || null;
    });
  }

  function guideDialog() { return document.getElementById('guide'); }
  function guideButton() { return document.getElementById('guide-btn'); }
  function tourAnchor(i) { return TOUR[i] ? document.querySelector(TOUR[i].anchor) : null; }
  function tabLive(id) { const t = TABS.find(x => x.id === id); return !!t && !t.later && !t.dim; }
  function tourAvailable(i) {  // a step is shown only while its part of the page is (never a pointer at nothing)
    if (TOUR[i] && TOUR[i].tab && !tabLive(TOUR[i].tab)) return false;
    const el = tourAnchor(i);
    return shown(el) && !el.closest('[inert], dialog:not([open])');
  }
  function tourStop(from, dir) {  // the next step in this direction whose part of the page is shown, else -1
    for (let i = from + dir; i >= 0 && i < TOUR.length; i += dir) if (tourAvailable(i)) return i;
    return -1;
  }
  // the card goes beside the visible part of what it points at: below, above, left, right, else inside
  // its top right corner (a part taller than the window); the ring outlines that visible part
  function placeTour() {
    tourFrame = 0;
    const st = window.Alpine.store('studio');
    const tour = st.ui.tour, card = document.getElementById('tour-card'), ring = document.getElementById('tour-ring');
    if (!tour || !card || !ring) return;
    if (!tourAvailable(tour.i)) { st.tourNext(); return; }  // its part was hidden meanwhile: move on
    const r = tourAnchor(tour.i).getBoundingClientRect();
    const vw = document.documentElement.clientWidth, vh = window.innerHeight, m = TOUR_GAP;
    const top = Math.max(r.top - TOUR_PAD, 4), bottom = Math.min(r.bottom + TOUR_PAD, vh - 4);
    const left = Math.max(r.left - TOUR_PAD, 4), right = Math.min(r.right + TOUR_PAD, vw - 4);
    Object.assign(ring.style, { top: top + 'px', left: left + 'px', width: Math.max(0, right - left) + 'px',
                                height: Math.max(0, bottom - top) + 'px' });
    const cw = card.offsetWidth, ch = card.offsetHeight;
    const x = v => Math.max(m, Math.min(v, vw - cw - m)), y = v => Math.max(m, Math.min(v, vh - ch - m));
    let at;
    if (bottom + m + ch <= vh - m) at = [x(left), bottom + m];
    else if (top - m - ch >= m) at = [x(left), top - m - ch];
    else if (left - m - cw >= m) at = [left - m - cw, y(top)];
    else if (right + m + cw <= vw - m) at = [right + m, y(top)];
    else at = [x(right - cw - m), y(top + m)];
    Object.assign(card.style, { left: at[0] + 'px', top: at[1] + 'px' });
  }
  function placeTourSoon() { if (!tourFrame) tourFrame = window.requestAnimationFrame(placeTour); }
  function watchTour(on) {  // scrolling (the page or the summary column) and resizing move the card along
    const opts = { capture: true, passive: true };
    if (on) {
      window.addEventListener('scroll', placeTourSoon, opts);
      window.addEventListener('resize', placeTourSoon, opts);
      if (typeof ResizeObserver === 'function') { tourResize = new ResizeObserver(placeTourSoon); tourResize.observe(document.body); }
      return;
    }
    window.removeEventListener('scroll', placeTourSoon, opts);
    window.removeEventListener('resize', placeTourSoon, opts);
    if (tourResize) { tourResize.disconnect(); tourResize = null; }
    if (tourFrame) { window.cancelAnimationFrame(tourFrame); tourFrame = 0; }
  }

  // --- store ----------------------------------------------------------------------------------
  function createStudio() {
    return {
      // answers: what the person picked, by section id (hidden sections keep theirs for later)
      answers: plainObject(storage.get('answers', {})),
      // sections / missing: the question list from /api/questions, in server order
      sections: [],
      missing: [],
      // builder: page state of the sections (NEW badges, open tips, custom colour fields, folding)
      builder: {
        loaded: false, error: '', previewError: '', announce: '',
        seen: null, fresh: [], byWords: [], tips: {},
        custom: { open: false, name: '', hex: '', hint: '' },  // hint: a colour word from the brief
        waiting: false,  // answers changed and the options or cost are not back yet
        // active: the section in use (the last one the person acted in, or opened with Change); it stays
        // open while answered sections fold. showAll: "Show all options" (this visit only)
        active: null, showAll: false,
      },
      // brief: the "What do you want to make?" box. picked: {section id: value} the last words filled in;
      // owned: every value words filled in this visit (new words may replace those the person left alone)
      brief: { text: '', busy: false, error: '', done: false, picked: {}, owned: {}, hint: '', provider: '', fallback: false },
      // preview: prompt, cost and route tier for complete answers from /api/preview
      preview: null,
      // jobs: this person's jobs from the server, newest first; watching: the id shown in the job panel
      jobs: [],
      watching: null,
      // chosen: the sketch picked as the layout of the next final ({file, n, shotId, caption, words});
      // it belongs to one shot, so any change to the answers clears it
      chosen: null,
      // run: the buttons' request and its answer (in-page confirm, reference packshot gate, error);
      // anchorJobs: reference packshots made from the gate this visit
      run: { busy: false, error: '', confirm: null, anchor: null, anchorJobs: [] },
      // panel: the job list's own state (loaded, load error, status checks given up)
      panel: { loaded: false, error: '', lost: false },
      // budget: figures from /api/health
      budget: { gemini_usd: 0, cap_usd: 9, cf_neurons: 0, cf_cap: 9500, loaded: false, error: '' },
      // ui: current tab, guide panel, tour ({i}: the step shown, or null)
      ui: { tabs: TABS, tab: 'create', note: '', guideOpen: false, tourSeen: storage.get('tourSeen', false), tour: null,
            promptOpen: false },
      // guide: the page shown in the guide, and its loading state
      guide: { pages: GUIDE_PAGES, later: GUIDE_LATER, page: GUIDE_PAGES[0].id, loading: false, error: '' },
      // review: the items from /api/reviews (newest first) and the Review tab's own state.
      // selected: the item shown; view: {id, key} of the image shown large, else its latest; busy: the item
      // id of the request in flight (one at a time); form: {id, kind, note, replace, from} for a verdict that
      // needs words or a Replace; picker: the item whose "Ask for a change" is open; msg: {id, text, err};
      // sizes: each image's own pixel size, to scale the check boxes drawn on it
      review: { items: [], loaded: false, error: '', lost: false, selected: null, view: null, busy: null, form: null,
                picker: null, msg: null, announce: '', sizes: {}, broken: {}, stepsOpen: false },
      // rules: from /api/rules (items in file order, scopes {value, label}). edits: {index: {text, scope, draft,
      // draftScope}} what the person typed, kept across reloads; busy: {index, what} of the request in flight (one at a
      // time); ask: the index whose Dismiss confirm is open; msg: {index, text, err}
      rules: { items: [], scopes: [], loaded: false, error: '', edits: {}, busy: null, ask: null, msg: null },
      // library: approved images from /api/library (newest approval first) and the filter values present among them.
      // filters: {key: value or ''}; selected: the item in the record drawer; busy: {id, pdf} of the export in flight;
      // msg: {id, text, err}; copied: {id, field, ok} the last copy button's answer; note: why an open record left
      library: { items: [], facets: {}, loaded: false, error: '', filters: { style: '', colour: '', technique: '',
                 placement: '', model: '', kind: '' }, selected: null, busy: null, msg: null, copied: null,
                 promptOpen: false, broken: {}, announce: '', note: '' },

      init() {
        this.refreshBudget();
        setInterval(() => this.refreshBudget(), BUDGET_REFRESH_MS);
        const c = this.answers.colour;
        if (c && typeof c === 'object') {
          Object.assign(this.builder.custom, { open: true, name: String(c.name || ''), hex: String(c.hex || '') });
        }
        // the first-visit tour starts once the builder has drawn its first options (or said it couldn't)
        this.loadQuestions().then(() => window.Alpine.nextTick(() => this.firstVisitTour()));
        this.loadJobs();
        this.loadReviews();  // the Review tab's count; checks still running are followed from here
        this.loadRules();  // the Review tab's rules badge
        // polling pauses while the browser tab is hidden and catches up when it is shown again
        document.addEventListener('visibilitychange', () => {
          if (document.hidden) { clearTimeout(reviewTimer); reviewTimer = null; }
          else if (this.review.loaded && !this.review.lost) this.loadReviews();
        });
      },

      // budget
      async refreshBudget() {
        try {
          const h = await api('/api/health');
          Object.assign(this.budget, {
            gemini_usd: h.gemini_usd, cap_usd: h.cap_usd, cf_neurons: h.cf_neurons, cf_cap: h.cf_cap,
            loaded: true, error: '',
          });
        } catch (e) {
          this.budget.error = 'The budget figure could not be loaded. It will retry in a minute.';
        }
      },
      budgetText() {
        if (!this.budget.loaded) return 'This month ...';
        return 'This month $' + this.budget.gemini_usd.toFixed(2) + ' of $' + Number(this.budget.cap_usd).toFixed(0);
      },
      budgetPct() {
        const b = this.budget;
        return b.cap_usd ? Math.min(100, Math.round((b.gemini_usd / b.cap_usd) * 100)) : 0;
      },

      // tabs and guide
      openTab(tab) {
        if (tab.dim) return;
        if (tab.later) { this.ui.note = tab.label + ': coming in a later step.'; return; }
        this.ui.note = '';
        this.ui.tab = tab.id;
        if (tab.id === 'review') {  // coming back to the tab checks again, even after losing touch
          this.reviewCheckAgain();
          this.loadRules();
        }
        if (tab.id === 'library') this.libraryRetry();  // and any image that would not load is tried again
      },
      tabLabel(t) {  // Review counts the images waiting for a person
        const waiting = REVIEW_GROUPS[0].states;
        const n = t.id === 'review' ? this.review.items.filter(i => waiting.includes(i.review || null)).length : 0;
        return n ? t.label + ' (' + n + ')' : t.label;
      },
      goTab(id) { const t = TABS.find(x => x.id === id); if (t) this.openTab(t); },
      tabBadge(t) {  // and, beside it, the rules waiting to be switched on
        const n = t.id === 'review' ? this.pendingRules().length : 0;
        return n ? n + (n === 1 ? ' rule' : ' rules') : '';
      },
      saveDraft() { storage.set('answers', this.answers); },

      // --- guide: a modal dialog (the browser keeps the keyboard inside it; Escape closes it) ------
      toggleGuide() { if (this.ui.guideOpen) this.closeGuide(); else this.openGuide(); },
      openGuide() {
        const d = guideDialog();
        if (!d) return;
        if (this.ui.tour) this.endTour(false);
        guideCache.clear();
        this.ui.guideOpen = true;
        if (!d.open) d.showModal();
        this.showGuidePage(this.guide.page);
        window.Alpine.nextTick(() => {  // the keyboard starts on the page being read, in the page list
          const cur = d.querySelector('.guide-nav [aria-current="page"]');
          if (cur) cur.focus();
        });
      },
      closeGuide() {
        const d = guideDialog();
        if (d && d.open) d.close();  // its close event (guideClosed) finishes the job
        else this.guideClosed();
      },
      guideClosed() {  // Close, Escape or "Replay the tour": the keyboard goes back to "? Guide"
        this.ui.guideOpen = false;
        if (this.ui.tour) return;  // the tour has the keyboard now
        const b = guideButton();
        if (b) b.focus();
      },
      // a click on the backdrop closes the guide, but not the end of a text selection dragged out of the page
      guidePress(e) { guidePressOutside = e.target === guideDialog(); },
      guideBackdrop(e) {
        const outside = e.target === guideDialog() && guidePressOutside;
        guidePressOutside = false;
        if (outside) this.closeGuide();
      },
      isGuidePage(p) { return this.guide.page === p.id; },
      async showGuidePage(id) {
        if (!GUIDE_PAGES.some(p => p.id === id)) return;  // only the server's fixed pages are ever asked for
        const seq = ++guideSeq;
        const doc = document.getElementById('guide-doc');
        Object.assign(this.guide, { page: id, error: '' });
        let md = guideCache.get(id);
        if (md === undefined) {
          if (doc) doc.replaceChildren();  // never the last page's text under this page's name
          this.guide.loading = true;
          try {
            const res = await fetch('/api/guide/' + encodeURIComponent(id));
            if (!res.ok) throw new Error('guide ' + res.status);
            md = await res.text();
          } catch (e) {
            if (seq === guideSeq) this.guide.error = GUIDE_ERROR;
            return;
          } finally {
            if (seq === guideSeq) this.guide.loading = false;
          }
          guideCache.set(id, md);
        }
        if (seq !== guideSeq || !doc) return;
        doc.replaceChildren(...markdownNodes(md));
        const box = doc.closest('.guide-page');
        if (box) box.scrollTop = 0;
      },
      replayTour() {  // the tour points at the builder: from another tab it goes there first
        this.closeGuide();
        if (this.ui.tab === 'create') { this.startTour(guideButton()); return; }
        Object.assign(this.ui, { tab: 'create', note: '' });
        window.Alpine.nextTick(() => window.requestAnimationFrame(() => this.startTour(guideButton())));
      },

      // --- first-visit tour: Next / Back / Skip; Escape ends it; it never covers or blocks the page --
      markTourSeen() { this.ui.tourSeen = true; storage.set('tourSeen', true); },
      firstVisitTour() {  // once per browser; once per page load at most when storage is unavailable
        if (this.ui.tourSeen || this.ui.tour || this.ui.guideOpen) return;
        this.startTour(null);
      },
      startTour(returnTo) {
        if (this.ui.tour) return;
        const i = tourStop(-1, 1);
        if (i < 0) return;  // nothing on the page to point at
        this.markTourSeen();
        const from = returnTo || document.activeElement;
        tourReturn = from && from !== document.body ? from : null;
        this.ui.tour = { i };
        watchTour(true);
        this.showTourStep(i);
      },
      showTourStep(i) {
        this.ui.tour.i = i;
        const el = tourAnchor(i);
        if (el) el.scrollIntoView({ block: 'nearest', behavior: scrollMode() });
        // on the next frame, when the card is drawn: Alpine runs a nextTick made inside another nextTick
        // (the first-visit start) at once, before the card is shown, and a hidden button takes no focus
        window.Alpine.nextTick(() => window.requestAnimationFrame(() => {
          if (!this.ui.tour) return;
          placeTour();
          const next = document.getElementById('tour-next');
          if (next) next.focus({ preventScroll: true });
        }));
      },
      tourNext() {
        if (!this.ui.tour) return;
        const j = tourStop(this.ui.tour.i, 1);
        if (j < 0) this.endTour(true);
        else this.showTourStep(j);
      },
      tourBack() {
        if (!this.ui.tour) return;
        const j = tourStop(this.ui.tour.i, -1);
        if (j >= 0) this.showTourStep(j);
      },
      endTour(giveBack) {  // giveBack: put the keyboard back where it was (else on the idea box)
        if (!this.ui.tour) return;
        watchTour(false);
        this.ui.tour = null;
        const back = tourReturn;
        tourReturn = null;
        if (giveBack === false) return;
        const target = back && back.isConnected && shown(back) ? back : document.getElementById('brief-text');
        if (target) target.focus();  // brought into view if the tour scrolled away from it
      },
      tourEscape() { if (this.ui.tour) this.endTour(true); },
      tourStep() { return this.ui.tour ? TOUR[this.ui.tour.i] : { title: '', text: '' }; },
      tourCount() {  // counts only the steps whose part of the page is shown
        if (!this.ui.tour) return '';
        const avail = TOUR.map((s, i) => i).filter(tourAvailable);
        const n = avail.indexOf(this.ui.tour.i) + 1;
        return n ? 'Tip ' + n + ' of ' + avail.length : '';
      },
      tourHasBack() { return !!this.ui.tour && tourStop(this.ui.tour.i, -1) >= 0; },
      tourNextLabel() { return this.ui.tour && tourStop(this.ui.tour.i, 1) < 0 ? 'Done' : 'Next'; },

      // --- builder: questions ---------------------------------------------------------------
      syncWaiting() {  // the summary dims and says "updating..." while this is true
        this.builder.waiting = !!(questionsTimer || previewTimer || questionsOpen || previewOpen);
      },
      askQuestions() {  // each change re-posts the answers, debounced
        clearTimeout(questionsTimer);
        clearTimeout(previewTimer);
        previewTimer = null;
        previewSeq++;  // a preview in flight is for older answers
        questionsTimer = setTimeout(() => { questionsTimer = null; this.loadQuestions(); }, QUESTIONS_DEBOUNCE_MS);
        this.syncWaiting();
      },
      async loadQuestions() {
        const seq = ++questionsSeq;
        questionsOpen = seq;
        this.syncWaiting();
        const sent = clone(this.answers);
        let res;
        try {
          res = await api('/api/questions', post({ answers: sent }));
        } catch (e) {
          if (seq === questionsSeq) {
            this.builder.error = NOTES.options;
            this.preview = null;  // its cost is for answers that have changed since
            briefJump = null;  // no fresh sections to move to
          }
          return;
        } finally {
          if (questionsOpen === seq) questionsOpen = 0;
          this.syncWaiting();
        }
        if (seq !== questionsSeq || questionsTimer) return;  // newer answers are on their way
        this.builder.error = '';
        this.applySections(res, sent);
        if (briefJump !== null) {  // words were just filled in: go to what is still missing
          const due = briefJump === touchSeq;
          briefJump = null;
          if (due) window.Alpine.nextTick(() => this.showFirstGap());
        }
        if (this.missing.length) {
          this.preview = null;
          this.builder.previewError = '';
        } else {
          this.askPreview();
        }
      },
      applySections(res, sent) {
        // drop what the server dropped (unless it was changed again since this request)
        const kept = new Set(res.sections.filter(s => s.value !== null && s.value !== undefined).map(s => s.id));
        const dropped = [];
        for (const k of Object.keys(sent)) {
          if (!kept.has(k) && same(this.answers[k], sent[k])) { delete this.answers[k]; dropped.push(k); }
        }
        if (dropped.length) this.saveDraft();
        // NEW badges: sections that were hidden on the last reply; kept until answered or hidden again
        const b = this.builder;
        const visible = res.sections.filter(s => s.visible).map(s => s.id);
        const added = b.seen ? visible.filter(id => !b.seen.includes(id)) : [];
        b.fresh = b.fresh.filter(id => visible.includes(id)).concat(added);
        b.byWords = b.byWords.filter(id => b.fresh.includes(id)).concat(wordsReveal ? added : []);
        wordsReveal = false;
        b.seen = visible;
        // a visible section whose answer was dropped has no answer now, so it opens again: say so
        const titles = ids => res.sections.filter(s => ids.includes(s.id)).map(s => s.title).join(', ');
        const lost = dropped.filter(id => visible.includes(id));
        const said = [];
        if (added.length) said.push('New questions: ' + titles(added) + '.');
        if (lost.length) said.push('Choose again: ' + titles(lost) + '.');
        if (said.length) b.announce = said.join(' ');
        this.sections = res.sections;
        this.missing = res.missing;
        b.loaded = true;
      },
      visibleSections() { return this.sections.filter(s => s.visible); },
      view(s) {
        if (s.kind === 'colour' || s.kind === 'size') return s.kind;
        return TILE_VIEWS[s.id] || 'seg';
      },
      isFresh(s) { return this.builder.fresh.includes(s.id); },
      newBadge(s) {
        if (this.builder.byWords.includes(s.id)) return 'NEW \u00b7 from your words';
        return NEW_REASONS[s.id] ? 'NEW \u00b7 ' + NEW_REASONS[s.id] : 'NEW';
      },
      hoodie(id) { return { href: HOODIES[id] || '#h-classic' }; },  // x-bind object for an SVG <use>
      tipLabel(s) { return 'More about \u201c' + s.title + '\u201d'; },
      toggleTip(s) { this.builder.tips[s.id] = !this.builder.tips[s.id]; },
      tipOpen(s) { return !!this.builder.tips[s.id]; },

      // --- builder: folding (ask only what is missing) ----------------------------------------
      // An answered section folds to one line ("Colour ✓ Olive · Change") unless it is the section in use
      // or "Show all options" is on. An unanswered section never folds, so one whose answer the server
      // dropped opens again by itself. Hidden (not applicable) sections stay hidden as before.
      isAnswered(s) { return this.optionLabel(s, this.answers[s.id]) !== ''; },
      isFolded(s) { return !this.builder.showAll && this.builder.active !== s.id && this.isAnswered(s); },
      canDone(s) { return !this.builder.showAll && this.builder.active === s.id && this.isAnswered(s); },
      anyAnswered() { return this.visibleSections().some(s => this.isAnswered(s)); },
      foldNote() {
        return this.builder.showAll ? 'Showing every option.'
          : 'Choices already made are folded; press Change to edit one.';
      },
      showAllLabel() { return this.builder.showAll ? 'Show only what\u2019s left' : 'Show all options'; },
      changeLabel(s) { return 'Change \u201c' + s.title + '\u201d, now ' + this.rowText(s); },
      doneLabel(s) { return 'Done with \u201c' + s.title + '\u201d'; },
      touch(id) {  // the person acted in (or opened) a section: it is the one in use; the last one may fold
        touchSeq++;
        if (this.builder.active === id) return;
        holdPlace(id);
        this.builder.active = id;
      },
      change(s) {  // "Change" on a folded row: open it in place and put the keyboard on its choice
        this.touch(s.id);
        window.Alpine.nextTick(() => this.focusChoice(s.id, null));
      },
      done(s) {  // "Done": fold the section in use and give the keyboard back to its row
        touchSeq++;
        if (this.builder.active === s.id) this.builder.active = null;
        window.Alpine.nextTick(() => {
          const row = document.getElementById('chg-' + s.id);
          if (shown(row)) row.focus();
        });
      },
      toggleShowAll() {  // "Show only what's left" folds every answered section, the one in use too
        touchSeq++;
        this.builder.showAll = !this.builder.showAll;
        this.builder.active = null;
      },
      focusChoice(id, block) {  // put the keyboard on a section's choice; block: also scroll it there
        const sec = sectionEl(id);
        if (!sec) return;
        if (block) sec.scrollIntoView({ behavior: scrollMode(), block });
        const body = document.getElementById('body-' + id);
        let target = null;
        if (id === 'colour' && this.builder.custom.open) {
          target = firstShown(body, this.builder.custom.name ? '#custom-colour .hex' : '#custom-colour input');
        }
        target = target || firstShown(body, '[aria-pressed="true"]:not(:disabled)')
          || firstShown(body, 'input:not(:disabled), .row button:not(:disabled), .seg button:not(:disabled)')
          || firstShown(sec, 'h4');  // nothing to pick (e.g. no maximum size yet): its heading
        if (target) target.focus({ preventScroll: !!block });
      },
      showFirstGap() {  // after words were filled in: the first section still to answer, else the summary
        const gap = this.visibleSections().find(s => !this.isAnswered(s));
        if (gap) {  // scrolled to only if its head is not already in the upper part of the screen,
          // so "Picked up: …" stays in sight when the gap is just below it
          const el = sectionEl(gap.id), top = el ? el.getBoundingClientRect().top : -1;
          this.focusChoice(gap.id, top >= 0 && top < window.innerHeight * 0.6 ? null : 'start');
          return;
        }
        const card = document.getElementById('summary');
        if (!shown(card)) return;
        card.scrollIntoView({ behavior: scrollMode(), block: 'nearest' });
        card.focus({ preventScroll: true });
      },

      // --- builder: answers -----------------------------------------------------------------
      setAnswer(id, value) {  // every pick, custom colour edit and size change by the person comes here
        const before = this.answers[id];
        if (value === null || value === undefined) delete this.answers[id];
        else this.answers[id] = value;
        if (!same(before, this.answers[id])) this.answersChanged();
        edits[id] = (edits[id] || 0) + 1;
        delete this.brief.owned[id];  // the person's own answer now: sent with the next words, and kept
        this.builder.fresh = this.builder.fresh.filter(x => x !== id);
        this.saveDraft();
        this.askQuestions();
      },
      isOn(s, o) {
        const v = this.answers[s.id];
        if (s.kind === 'colour' && this.builder.custom.open) return false;
        return v !== undefined && v !== null && typeof v !== 'object' && String(v) === o.id;
      },
      pick(s, o) {
        if (o.disabled) return;
        this.touch(s.id);
        if (s.kind === 'colour') Object.assign(this.builder.custom, { open: false, hint: '' });
        this.setAnswer(s.id, NUMERIC.has(s.id) ? Number(o.id) : o.id);
      },
      subLine(s, o) {  // the small line under an option: why it is greyed, or its fact
        if (o.disabled) return o.reason;
        return HIDE_SUB.has(s.id) ? '' : o.sub;
      },

      // colour: palette swatches plus a custom name and hex
      swatch(o) { return HEX_RE.test(o.sub) ? o.sub : 'transparent'; },
      colourHex() {  // the chosen colour, for the hoodie drawings
        const v = this.answers.colour;
        if (v && typeof v === 'object') return HEX_RE.test(v.hex) ? v.hex : NO_COLOUR;
        const sec = this.sections.find(s => s.id === 'colour');
        const o = sec && sec.options.find(x => x.id === v);
        return o && HEX_RE.test(o.sub) ? o.sub : NO_COLOUR;
      },
      openCustom() {
        this.touch('colour');
        this.builder.custom.open = true;
        this.applyCustom();
      },
      setCustom(field, value) {  // the colour section stays open while its name and hex are typed
        this.touch('colour');
        this.builder.custom[field] = value;
        this.applyCustom();
      },
      customProblem() {
        const c = this.builder.custom;
        if (!c.name.trim()) return 'Give the colour a name.';
        if (c.name.length > MAX_COLOUR_NAME) return 'Keep the name to ' + MAX_COLOUR_NAME + ' characters.';
        if (!HEX_RE.test(c.hex)) return 'The hex code is # and six characters from 0-9 and A-F, such as #1F6F78.';
        return '';
      },
      customTouched() { const c = this.builder.custom; return c.name !== '' || c.hex !== ''; },
      customHint() {  // a colour word from the brief waits for its hex: never filled in for the person
        const c = this.builder.custom;
        if (!c.hint || HEX_RE.test(c.hex)) return '';
        return '\u201c' + c.hint + '\u201d came from your words; pick the exact shade with a hex code.';
      },
      customMessage() {  // the line under the custom fields: what is wrong, unless the brief's note says it
        if (!this.customTouched()) return '';
        if (this.customHint() && this.builder.custom.hex === '') return '';
        return this.customProblem();
      },
      customSwatch() { const h = this.builder.custom.hex; return HEX_RE.test(h) ? h : 'transparent'; },
      applyCustom() {  // a valid custom colour is the answer; until then there is no colour answer
        const c = this.builder.custom;
        if (!this.customProblem()) this.setAnswer('colour', { name: c.name.trim(), hex: c.hex });
        else if (this.answers.colour !== undefined) this.setAnswer('colour', null);
      },

      // size: a slider from 1 to the placement's maximum
      sizeValue(s) { const v = this.answers.size_cm; return typeof v === 'number' ? v : s.max; },
      sizeText() { const v = this.answers.size_cm; return typeof v === 'number' ? v + ' cm' : 'not set yet'; },
      sizeLimit(s) {
        const where = this.answers.placement ? this.answers.placement + ' ' : '';
        return '\u00b7 ' + where + 'maximum is ' + Number(s.max) + ' cm, so larger sizes are not offered';
      },
      setSize(value) {  // the size section stays open while the slider is moved
        this.touch('size_cm');
        const n = Number(value);
        if (n > 0 && n !== this.answers.size_cm) this.setAnswer('size_cm', n);
      },
      sizeKey(e) {  // End or an arrow at the limit changes nothing, so no input event: take the value here.
        if (SIZE_KEYS.has(e.key)) this.setSize(e.target.value);  // not Tab: arriving on the slider is no choice
      },

      // --- builder: preview (summary and cost are drawn by task 6) ---------------------------
      liveAnswers() {  // what preview and jobs send: the answers of visible sections only
        const out = {};
        for (const s of this.sections) {
          if (s.visible && this.answers[s.id] !== undefined) out[s.id] = this.answers[s.id];
        }
        return out;
      },
      askPreview() {
        clearTimeout(previewTimer);
        const seq = ++previewSeq;
        previewTimer = setTimeout(() => { previewTimer = null; this.loadPreview(seq); }, PREVIEW_DEBOUNCE_MS);
        this.syncWaiting();
      },
      async loadPreview(seq) {
        if (seq !== previewSeq) { this.syncWaiting(); return; }
        previewOpen = seq;
        this.syncWaiting();
        try {
          const p = await api('/api/preview', post({ answers: this.liveAnswers() }));
          if (seq !== previewSeq) return;
          this.preview = p;
          this.builder.previewError = '';
        } catch (e) {
          if (seq !== previewSeq) return;
          this.preview = null;
          this.builder.previewError = NOTES.preview;
        } finally {
          if (previewOpen === seq) previewOpen = 0;
          this.syncWaiting();
        }
      },

      // --- fill from my words -----------------------------------------------------------------
      async fillFromWords() {
        const b = this.brief;
        const text = b.text.trim().slice(0, MAX_BRIEF);
        if (!text || b.busy) return;
        Object.assign(b, { busy: true, error: '', done: false, picked: {}, hint: '', provider: '', fallback: false });
        const before = clone(this.answers), seen = { ...edits };  // to leave alone what the person changes meanwhile
        const acts = touchSeq;  // the person's acts so far: one made while the words are read keeps its section open
        // The server keeps every answer it is sent, so only the person's own picks of visible sections go:
        // hidden sections' old answers and earlier words' picks the person never set can be replaced by new words.
        const sent = {}, live = clone(this.liveAnswers());
        for (const k of Object.keys(live)) {
          if (!(k in b.owned && same(live[k], b.owned[k]))) sent[k] = live[k];
        }
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), BRIEF_TIMEOUT_MS);
        let res = null, data = null;
        try {
          res = await fetch('/api/brief', { ...post({ text, answers: sent }), signal: ctrl.signal });
          if (res.ok) data = await res.json();
        } catch (e) {
          b.error = e && e.name === 'AbortError' ? NOTES.briefSlow : NOTES.briefFailed;
          return;
        } finally {
          clearTimeout(timer);
          b.busy = false;
        }
        if (!res.ok || !data || typeof data !== 'object') {
          b.error = res.status === 503 ? NOTES.briefBusy : NOTES.briefFailed;
          return;
        }
        this.applyBrief(data, sent, before, seen, acts);
      },
      applyBrief(data, sent, before, seen, acts) {
        const b = this.brief, got = plainObject(data.answers);
        const untouched = k => edits[k] === seen[k] && same(this.answers[k], before[k]);  // not set during the request
        let changed = false;
        for (const k of Object.keys(got)) {
          if (same(got[k], sent[k])) continue;  // the person's own answer, sent and kept
          if (!untouched(k)) continue;  // set by the person while the words were read
          if (same(got[k], this.answers[k])) continue;  // the same as already there: no change, no chip
          this.answers[k] = got[k];
          changed = true;
          b.picked[k] = got[k];
          b.owned[k] = got[k];
          this.builder.fresh = this.builder.fresh.filter(x => x !== k);
        }
        if (changed) this.answersChanged();
        if ('colour' in b.picked) Object.assign(this.builder.custom, { open: false, hint: '' });
        const hint = typeof data.colour_hint === 'string' ? data.colour_hint.trim().slice(0, MAX_COLOUR_NAME) : '';
        // a colour outside the samples: open Custom with its name and an empty hex (never a guessed one),
        // unless the person has their own colour or set it while the words were read
        if (hint && !('colour' in sent) && untouched('colour')) {
          Object.assign(this.builder.custom, { open: true, name: hint, hex: '', hint });
          b.hint = hint;
          this.applyCustom();  // no valid hex yet: any colour from earlier words is cleared
        }
        b.provider = PROVIDERS[data.provider] ? data.provider : '';
        b.fallback = data.fallback === true;
        b.done = true;
        wordsReveal = true;
        // every answered section folds and the next questions reply moves the page to the first gap,
        // unless the person acted in the builder while the words were read: their section stays as it is
        if (acts === touchSeq) {
          this.builder.active = null;
          briefJump = touchSeq;
        }
        this.saveDraft();
        this.askQuestions();
      },
      pickedChips() {  // what the words filled in, as plain words, in section order
        const p = this.brief.picked, out = [];
        const where = 'placement' in p && p.decoration === 'design';  // one chip: "design on the full back"
        for (const s of this.sections) {
          if (!(s.id in p) || (where && s.id === 'placement')) continue;
          let t = this.optionLabel(s, p[s.id]);
          if (!t) continue;
          if (CHIP_LOWER.has(s.id)) t = t.toLowerCase();
          if (where && s.id === 'decoration') {
            const pl = this.sections.find(x => x.id === 'placement');
            const at = pl ? this.optionLabel(pl, p.placement) : '';
            if (at) t = 'design on the ' + at.toLowerCase();
          } else if (s.id === 'placement') {
            t = 'on the ' + t;
          }
          out.push(t);
        }
        return out;
      },
      briefFound() {  // the colour word counts while the person keeps that custom colour
        return this.brief.done && (this.pickedChips().length > 0 || !!this.builder.custom.hint);
      },
      briefNothing() {
        return this.brief.done && !this.pickedChips().length && !this.brief.hint ? NOTES.briefNothing : '';
      },
      providerText() {
        const name = PROVIDERS[this.brief.provider];
        if (!this.brief.done || !name) return '';
        return 'Answered by ' + name + (this.brief.fallback ? ' (backup)' : '');
      },

      // --- summary ("Your image") -------------------------------------------------------------
      optionLabel(s, v) {  // an answer in plain words: the option's label, never its id
        if (v === undefined || v === null || v === '') return '';
        if (s.kind === 'colour' && typeof v === 'object') return String(v.name || '');
        if (s.kind === 'size') return typeof v === 'number' ? v + ' cm' : '';
        const o = s.options.find(x => x.id === String(v));
        return o ? o.label : '';
      },
      summaryRows() { return this.visibleSections().filter(s => this.optionLabel(s, this.answers[s.id])); },
      rowLabel(s) { return ROW_LABELS[s.id] || s.title; },
      rowText(s) {  // a design adds its "for dark/light garments" note: the two variants share a name
        const v = this.answers[s.id];
        if (s.kind === 'colour' && v && typeof v === 'object') return v.name + ' \u00b7 ' + String(v.hex).toUpperCase();
        const t = capital(this.optionLabel(s, v));
        const o = t && this.view(s) === 'design' ? s.options.find(x => x.id === String(v)) : null;
        return o && o.sub ? t + ' \u00b7 ' + o.sub : t;
      },
      missingSections() { return this.sections.filter(s => s.visible && this.missing.includes(s.id)); },
      editSection(id) {  // open a section (folded or not), scroll to it and put the keyboard on its choice
        if (!sectionEl(id)) return;
        this.touch(id);
        window.Alpine.nextTick(() => this.focusChoice(id, 'start'));
      },
      costText() { return this.preview ? money(this.preview.base_usd) : ''; },
      worstText() { return this.preview ? 'up to ' + money(this.preview.worst_usd) + ' if every allowed fix runs' : ''; },
      tierNote() {  // from the router's tier; nothing is shown if the preview has none
        const p = this.preview;
        const name = p && { premium: 'Premium model', standard: 'Standard model' }[p.tier];
        if (!name) return '';
        let t = name;
        if (p.tier === 'premium' && this.liveAnswers().decoration === 'design') t += ' because it has a design';
        else if (p.reason && p.action === 'run') t += ' \u00b7 ' + p.reason;
        return p.action === 'run' ? t + ' \u00b7 within budget' : t;
      },
      routeNote() {  // why this can't run as it is (a split or a daily limit used up)
        const p = this.preview;
        return p && p.action !== 'run' ? capital(p.reason || "This can't run within the budget right now.") : '';
      },
      anchorNote() {
        return this.preview && this.preview.needs_anchor
          ? 'This colour has no approved reference packshot yet; you will be asked to make it first.' : '';
      },
      togglePromptBox() { this.ui.promptOpen = !this.ui.promptOpen; },

      // --- jobs: the list, watching and polling ------------------------------------------------
      async loadJobs() {
        let res;
        try {
          res = await api('/api/jobs?limit=' + JOB_LIST);
        } catch (e) {
          this.panel.error = JOB_NOTES.list;
          return;
        }
        this.jobs = (Array.isArray(res.jobs) ? res.jobs : []).filter(j => j && Number.isInteger(j.id));
        Object.assign(this.panel, { loaded: true, error: '', lost: false });
        pollFails = 0;
        // a reloaded page watches the newest job still being made, and checks on every active one
        const active = this.jobs.find(j => ACTIVE.has(j.status));
        if (!this.watchedJob()) this.watching = active ? active.id : null;
        this.schedulePoll();
      },
      putJob(job) {  // a fresh copy of one job from the server replaces the old one
        if (!job || typeof job !== 'object' || !Number.isInteger(job.id)) return;
        const i = this.jobs.findIndex(j => j.id === job.id);
        const was = i >= 0 ? this.jobs[i].status : null;
        if (i >= 0) this.jobs.splice(i, 1, job);
        else this.jobs.unshift(job);
        if (ACTIVE.has(was) && !ACTIVE.has(job.status)) {
          this.refreshBudget();  // it may have spent
          if (job.kind === 'final' && job.status === 'done') this.loadReviews();  // its checks start now
        }
      },
      dropJob(id) {
        this.jobs = this.jobs.filter(j => j.id !== id);
        if (this.watching === id) this.watching = null;
      },
      activeJobs() { return this.jobs.filter(j => ACTIVE.has(j.status)); },
      schedulePoll() {  // every 2 s while any job is queued or running; stops when none is
        if (pollTimer || this.panel.lost || !this.activeJobs().length) return;
        pollTimer = setTimeout(() => { pollTimer = null; this.pollJobs(); }, POLL_MS);
      },
      async pollJobs() {
        if (pollBusy) return;  // the check in flight schedules the next one
        pollBusy = true;
        try {
          for (const j of this.activeJobs()) {
            let res;
            try {
              res = await fetch('/api/jobs/' + j.id);
            } catch (e) {
              res = null;
            }
            if (res && res.status === 404) { this.dropJob(j.id); continue; }  // gone from the job store
            const fresh = res && res.ok ? await res.json().catch(() => null) : null;
            if (!fresh) {
              if (++pollFails >= POLL_FAILS) { this.panel.lost = true; return; }
              continue;
            }
            pollFails = 0;
            this.putJob(fresh);
          }
        } finally {
          pollBusy = false;
          this.schedulePoll();
        }
      },
      checkAgain() { Object.assign(this.panel, { lost: false, error: '' }); this.loadJobs(); },
      lostText() { return this.panel.lost ? JOB_NOTES.lost : ''; },
      watchedJob() { return this.watching === null ? null : this.jobs.find(j => j.id === this.watching) || null; },
      watchedList() { const j = this.watchedJob(); return j ? [j] : []; },  // x-for over it names the job
      watchJob(j) { this.watching = j.id; },
      recentJobs() { return this.jobs.slice(0, RECENT); },
      answersChanged() {  // sketches, the chosen layout and the buttons' answers belong to the old shot
        this.chosen = null;
        Object.assign(this.run, { error: '', confirm: null, anchor: null });
        const w = this.watchedJob();
        if (w && !ACTIVE.has(w.status)) this.watching = null;  // a job still being made stays in view
      },

      // --- jobs: sketches and approve & create --------------------------------------------------
      currentShot() {  // the shot on screen, once its cost is back for the current answers
        return this.preview && !this.builder.waiting && !this.missing.length ? this.preview.shot_id : null;
      },
      shotActive(kind) {  // a job of this kind for the shot on screen is still being made
        const sid = this.currentShot();
        return !!sid && this.jobs.some(j => j.kind === kind && j.shot_id === sid && ACTIVE.has(j.status));
      },
      canSketch() { return !!this.currentShot() && !this.run.busy && !this.shotActive('drafts'); },
      canCreate() {
        return !!this.currentShot() && !this.run.busy && this.preview.action === 'run' && !this.shotActive('final');
      },
      sketchLabel() { return this.shotActive('drafts') ? 'Making sketches…' : 'See 3 free sketches'; },
      createLabel() { return this.shotActive('final') ? 'Creating your image…' : 'Approve & create'; },
      // whether the shot on screen's final may build on a sketch (the preview's takes_sketch, from the server)
      takesSketch() { return !!this.preview && this.preview.takes_sketch !== false; },
      layout() {  // the chosen sketch, only while it belongs to the shot on screen and that shot takes one
        const c = this.chosen;
        return c && c.shotId === this.currentShot() && this.takesSketch() ? c.file : null;
      },
      layoutNote() {
        if (this.currentShot() && !this.takesSketch()) return JOB_NOTES.noSketch;
        return this.layout() ? 'Builds on sketch ' + this.chosen.n + ', your chosen layout.' : '';
      },
      makeSketches() {
        if (!this.canSketch()) return null;
        const answers = this.liveAnswers();  // sketches take no layout: only the answers must still match
        return this.sendJob({ answers, kind: 'drafts', confirm: false, layout: null }, { answers });
      },
      approve() {
        if (!this.canCreate()) return null;
        const answers = this.liveAnswers(), layout = this.layout();
        return this.sendJob({ answers, kind: 'final', confirm: false, layout }, { answers, layout });
      },
      // screen: what the page showed when a request was made ({answers, layout?}). A confirm panel or a
      // packshot gate is only shown, and only acted on, while the page still shows exactly that.
      onScreen(screen) {
        if (!screen || !sameAnswers(screen.answers, this.liveAnswers())) return false;
        return screen.layout === undefined || (screen.layout || null) === (this.layout() || null);
      },
      choicesChanged() {
        Object.assign(this.run, { confirm: null, anchor: null, error: JOB_NOTES.changed });
      },
      confirmCreate() {  // "Create" in the in-page confirm: the same request, confirmed
        const c = this.run.confirm;
        if (!c) return null;
        if (!this.onScreen(c.screen)) { this.choicesChanged(); return null; }
        return this.sendJob({ ...c.body, confirm: true }, c.screen);
      },
      cancelConfirm() { this.run.confirm = null; },
      confirmText() {
        const c = this.run.confirm;
        return c ? 'This will cost about ' + money(c.usd) + '. Create it?' : '';
      },
      async sendJob(body, screen) {  // one request at a time: the buttons are disabled until it is answered
        if (this.run.busy) return null;
        this.run.busy = true;
        Object.assign(this.run, { error: '', confirm: null });
        try {
          return await this.postJob(body, screen);
        } finally {
          this.run.busy = false;
        }
      },
      async postJob(body, screen) {
        let res, data;
        try {
          res = await fetch('/api/jobs', post(body));
          data = plainObject(await res.json().catch(() => null));
        } catch (e) {
          this.run.error = JOB_NOTES.unreachable;
          return null;
        }
        if (res.status === 201 && Number.isInteger(data.id)) {
          await this.watchNew(data.id, body);
          return data.id;
        }
        // an answer about choices the page no longer shows (changed while this was on its way) is dropped
        if (!this.onScreen(screen)) {
          this.choicesChanged();
        } else if (res.status === 409 && data.needs_confirm === true && Number.isFinite(Number(data.estimate_usd))) {
          this.run.confirm = { usd: Number(data.estimate_usd), body, screen };
        } else if (res.status === 409 && data.needs_anchor === true) {
          this.showAnchorGate(data, screen);
        } else {
          this.run.error = typeof data.error === 'string' && data.error.trim() ? capital(data.error.trim()) : JOB_NOTES.refused;
        }
        return null;
      },
      async watchNew(id, body) {  // a job just made: in the list and the panel before the buttons come back
        let job = null;
        try { job = await api('/api/jobs/' + id); } catch (e) { job = null; }
        this.putJob(job || { id, kind: body.kind, status: 'queued', answers: body.answers, shot_id: this.currentShot(),
                             result: null, error: null, caption: '', created: new Date().toISOString() });
        this.watching = id;
        this.schedulePoll();
        window.Alpine.nextTick(() => {  // bring its status into view (it sits under the buttons)
          const panel = document.querySelector('.jobp');
          if (panel) panel.scrollIntoView({ block: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' });
        });
      },

      // reference packshot gate: the shot needs an approved packshot of its colour first.
      // gate: {message, answers (the packshot's), screen (the shot it was asked for), cost, shotId, checking}
      async showAnchorGate(data, screen) {
        const gate = { message: String(data.error || ''), answers: plainObject(data.anchor_answers),
                       screen: { answers: screen.answers }, cost: '', shotId: '', checking: true };
        this.run.anchor = gate;
        const mine = () => this.run.anchor && sameAnswers(this.run.anchor.answers, gate.answers);
        try {  // its cost and shot id, from the same preview the summary uses (no image is made)
          const p = await api('/api/preview', post({ answers: gate.answers }));
          if (mine() && typeof p.base_usd === 'number') this.run.anchor.cost = money(p.base_usd);
          if (mine() && typeof p.shot_id === 'string') this.run.anchor.shotId = p.shot_id;
        } catch (e) {
          /* the button keeps its fixed wording; the packshot is still found by its answers */
        } finally {
          if (mine()) this.run.anchor.checking = false;
        }
      },
      anchorJob() {  // the packshot this gate would make, if one is being made or awaits approval
        const a = this.run.anchor;
        if (!a) return null;
        const mine = j => j.kind === 'final' && ((a.shotId && j.shot_id === a.shotId) || sameAnswers(j.answers, a.answers));
        return this.jobs.find(j => mine(j) && ACTIVE.has(j.status))
          || this.jobs.find(j => mine(j) && j.status === 'done') || null;
      },
      anchorStatus() {  // replaces the button: never a second packshot while one is made or awaits approval
        const j = this.anchorJob();
        if (!j) return '';
        return ACTIVE.has(j.status) ? JOB_NOTES.anchor : JOB_NOTES.anchorDone;
      },
      canMakeAnchor() {
        const a = this.run.anchor;
        return !!a && !a.checking && !this.run.busy && !this.anchorJob();
      },
      anchorButton() {
        const a = this.run.anchor;
        if (a && a.checking) return 'Make the reference packshot first (checking the cost…)';
        return 'Make the reference packshot first (' + (a && a.cost ? 'about ' + a.cost : 'a standard image') + ')';
      },
      async makeAnchor() {
        const a = this.run.anchor;
        if (!this.canMakeAnchor()) return null;
        if (!this.onScreen(a.screen)) { this.choicesChanged(); return null; }
        // the gate stays open: its button becomes this packshot's status
        const id = await this.sendJob({ answers: a.answers, kind: 'final', confirm: false, layout: null }, a.screen);
        if (id !== null) this.run.anchorJobs.push(id);
        return id;
      },

      // --- jobs: what the panel and the list show -----------------------------------------------
      note(key) { return JOB_NOTES[key] || ''; },
      isActive(j) { return ACTIVE.has(j.status); },
      isAnchor(j) { return this.run.anchorJobs.includes(j.id); },
      files(j) { return jobFiles(j); },
      imgSrc(path) { return { src: fileUrl(path) }; },  // x-bind object: the URL is encoded, never raw text
      statusWord(j) { return STATUS_WORDS[j.status] || ''; },
      statusClass(j) { return STATUS_WORDS[j.status] ? 's-' + j.status : ''; },
      jobWords(j) {  // the shot in plain words, from the option labels: "olive oversized pullover hoodie, on model, front"
        const a = plainObject(j && j.answers);
        const label = (id, v) => {
          const s = this.sections.find(x => x.id === id);
          return (s && this.optionLabel(s, v)) || (typeof v === 'string' ? v.replace(/-/g, ' ') : '');
        };
        const garment = [label('colour', a.colour), label('catalogue', a.catalogue)].filter(Boolean).join(' ');
        const design = a.decoration === 'design' ? label('design', a.design) : '';
        return [garment.toLowerCase(), label('recipe', a.recipe).toLowerCase(), design].filter(Boolean).join(', ');
      },
      jobTitle(j) { return capital(this.jobWords(j)); },
      jobKind(j) {
        if (j.kind === 'drafts') return '3 free sketches';
        return this.isAnchor(j) ? 'Reference packshot' : 'Final image';
      },
      jobMeta(j) {
        const parts = [this.jobKind(j)];
        const r = j.result && typeof j.result.cost_usd === 'number' ? j.result.cost_usd : null;
        if (j.kind === 'final' && r !== null) parts.push(money(r));
        parts.push(clockText(j.created));
        return parts.filter(Boolean).join(' · ');
      },
      activeText(j) {
        if (this.panel.lost) return JOB_NOTES.unchecked;
        if (j.status === 'queued') return JOB_NOTES.queued;
        if (j.kind === 'drafts') return JOB_NOTES.drafts;
        return this.isAnchor(j) ? JOB_NOTES.anchor : JOB_NOTES.final;
      },
      errorText(j) { return j.error ? capital(String(j.error)) : JOB_NOTES.noError; },
      jobHex(j) {  // the job's colour, for the small hoodie drawing in the list
        const v = plainObject(j && j.answers).colour;
        if (v && typeof v === 'object') return HEX_RE.test(v.hex) ? v.hex : NO_COLOUR;
        const sec = this.sections.find(s => s.id === 'colour');
        const o = sec && sec.options.find(x => x.id === v);
        return o && HEX_RE.test(o.sub) ? o.sub : NO_COLOUR;
      },
      jobThumb(j) { const f = jobFiles(j); return j.status === 'done' && f.length ? f[0] : ''; },
      thumbFiles(j) { const f = this.jobThumb(j); return f ? [f] : []; },  // keyed: x-bind objects bind once
      canChoose(j) { return j.kind === 'drafts' && j.status === 'done' && !!j.shot_id && j.shot_id === this.currentShot(); },
      isChosen(f) { return !!this.chosen && this.chosen.file === f; },
      choose(j, f, i) {  // a second click on the chosen sketch un-picks it
        if (!this.canChoose(j)) return;
        this.run.confirm = null;  // a confirm was for the old layout
        this.chosen = this.isChosen(f) ? null
          : { file: f, n: i + 1, shotId: j.shot_id, caption: j.caption || '', words: this.jobWords(j) };
      },
      sketchAlt(j, i) { return 'Sketch ' + (i + 1) + ' of ' + this.jobWords(j); },
      sketchHint(j) {
        if (!this.canChoose(j)) return JOB_NOTES.otherShot;
        return this.takesSketch() ? JOB_NOTES.pick : JOB_NOTES.lookOnly;
      },
      tileText(f, i) {  // a sketch tile's words; a shot made without a sketch only shows the picked one larger
        if (!this.isChosen(f)) return 'Sketch ' + (i + 1);
        return this.takesSketch() ? '✓ chosen layout' : '✓ shown above';
      },
      finalNote(j) { return this.isAnchor(j) ? JOB_NOTES.anchorDone : JOB_NOTES.review; },
      // the big preview's image and drawing as keyed lists of one: Alpine evaluates an x-bind object once,
      // so a new file or style must make a new element (else the old src or shape would stay)
      shownFiles() { const s = this.shownImage(); return s ? [s.file] : []; },
      drawnStyle() { const c = this.answers.catalogue; return !this.shownImage() && typeof c === 'string' && c ? [c] : []; },
      shownImage() {  // the big preview box: the watched job's finished final, else the chosen sketch
        const w = this.watchedJob();
        if (w && w.kind === 'final' && w.status === 'done' && jobFiles(w).length) {
          return { file: jobFiles(w)[0], alt: (this.isAnchor(w) ? 'Reference packshot: ' : 'Final image: ') + this.jobWords(w),
                   label: this.jobKind(w), caption: w.caption || '', note: this.finalNote(w) };
        }
        const c = this.chosen;
        if (c) {
          const role = c.shotId === this.currentShot() && !this.takesSketch() ? 'for looking only' : 'your chosen layout';
          return { file: c.file, alt: 'Sketch ' + c.n + ' of ' + c.words, caption: c.caption,
                   label: 'Sketch ' + c.n + ' · ' + role + ' · free, never published', note: '' };
        }
        return null;
      },
      openReview(id) {  // "Open in Review" on a finished image: the Review tab with that item shown
        this.selectReview(id);
        this.openTab(TABS.find(t => t.id === 'review'));
      },

      // --- review: the list, polling every 3 s while anything is checked or fixed ---------------------
      async loadReviews() {
        clearTimeout(reviewTimer);
        reviewTimer = null;
        const seq = ++reviewSeq, since = reviewEpoch;
        let res;
        try {
          res = await api('/api/reviews');
        } catch (e) {
          if (seq !== reviewSeq) return;
          if (!this.review.loaded) this.review.error = REVIEW_NOTES.list;
          else if (++reviewFails >= POLL_FAILS) this.review.lost = true;
          else this.scheduleReviewPoll();
          return;
        }
        if (seq !== reviewSeq) return;  // a newer list is on its way
        reviewFails = 0;
        this.setReviews(res.items, since);
        this.scheduleReviewPoll();
      },
      setReviews(items, since) {
        const old = new Map(this.review.items.map(i => [i.id, i]));
        const fresh = (Array.isArray(items) ? items : []).filter(i => i && Number.isInteger(i.id))
          .map(i => ((reviewActed.get(i.id) || 0) > since && old.has(i.id) ? old.get(i.id) : i));
        fresh.forEach(i => this.unbreak(old.get(i.id), i));
        if (this.review.loaded) {  // say what changed by itself (a person's own click is answered in place)
          const moved = fresh.filter(i => !old.has(i.id) || old.get(i.id).review !== i.review);
          const said = moved.map(i => this.statusLine(i));
          if (said.length) this.review.announce = said.length > 3 ? said.length + ' images changed in the review list.' : said.join(' ');
          const m = this.review.msg;  // an earlier answer ("the fix is running…") is out of date once its item moved on
          if (m && moved.some(i => i.id === m.id)) this.review.msg = null;
        }
        Object.assign(this.review, { items: fresh, loaded: true, error: '', lost: false });
        if (!this.selectedReview()) this.review.selected = this.firstReview();
      },
      putReview(item, announce) {  // a fresh copy of one item (an action's reply, or one read again)
        if (!item || !Number.isInteger(item.id)) return;
        reviewActed.set(item.id, ++reviewEpoch);
        const i = this.review.items.findIndex(x => x.id === item.id);
        const was = i >= 0 ? this.review.items[i].review : undefined;
        this.unbreak(i >= 0 ? this.review.items[i] : null, item);
        if (i >= 0) this.review.items.splice(i, 1, item);
        else this.review.items.unshift(item);
        if (announce && was !== item.review) this.review.announce = this.statusLine(item);
        if (busyReview(item)) this.scheduleReviewPoll();
      },
      async refreshReview(id) {  // after a refusal: the item as it stands now (decided by someone else, running…)
        let res;
        try { res = await fetch('/api/reviews/' + id); } catch (e) { return; }
        if (res.status === 404) {
          this.review.items = this.review.items.filter(i => i.id !== id);
          if (this.review.selected === id) this.review.selected = this.firstReview();
          return;
        }
        const item = res.ok ? await res.json().catch(() => null) : null;
        if (item) this.putReview(item, true);
      },
      scheduleReviewPoll() {
        if (reviewTimer || this.review.lost || document.hidden || !this.review.items.some(busyReview)) return;
        reviewTimer = setTimeout(() => { reviewTimer = null; this.loadReviews(); }, REVIEW_POLL_MS);
      },
      reviewCheckAgain() {  // also tries again any image that would not load
        Object.assign(this.review, { lost: false, error: '', broken: {} });
        reviewFails = 0;
        this.loadReviews();
      },
      unbreak(was, now) {  // an item that changed may have its images back: they are tried again
        if (!was || was.updated === now.updated) return;
        for (const p of [now.layout, now.latest, ...(Array.isArray(now.images) ? now.images : [])]) {
          if (p && this.review.broken[p]) delete this.review.broken[p];
        }
      },
      reviewLostText() { return this.review.lost ? REVIEW_NOTES.lost : ''; },
      reviewNote(key) { return REVIEW_NOTES[key] || ''; },
      reviewGroups() {
        return REVIEW_GROUPS.map(g => ({ id: g.id, title: g.title,
                                         items: this.review.items.filter(i => g.states.includes(i.review || null)) }))
          .filter(g => g.items.length);
      },
      firstReview() { const g = this.reviewGroups()[0]; return g ? g.items[0].id : null; },
      selectedReview() { return this.review.items.find(i => i.id === this.review.selected) || null; },
      selectedReviews() { const it = this.selectedReview(); return it ? [it] : []; },  // x-for over it names the item
      selectReview(id) {
        Object.assign(this.review, { selected: id, view: null, form: null, picker: null, msg: null });
      },
      reviewWord(it) { return REVIEW_WORDS[it.review] || 'Not checked yet'; },
      statusLine(it) { return it.words + ': ' + this.reviewWord(it).replace(/…$/, '') + '.'; },  // for the live region
      reviewClass(it) { return REVIEW_CLASS[it.review] || ''; },
      reviewMeta(it) { return [clockText(it.created), smallMoney(it.cost_usd)].filter(Boolean).join(' · '); },
      reviewByline(it) { return ['Asked for by ' + it.user, clockText(it.created)].filter(Boolean).join(' · '); },
      reviewThumb(it) { return it.latest && !this.review.broken[it.latest] ? [it.latest] : []; },  // keyed: x-bind binds once

      // --- review: progress line --------------------------------------------------------------------
      reviewStages(it) {  // Sketches → Final → Checks → Fixes → Your review → Export
        const steps = Array.isArray(it.steps) ? it.steps : [];
        const checks = steps.filter(s => s.kind === 'check'), fixes = steps.filter(s => s.kind !== 'check');
        const doneFixes = fixes.filter(s => s.status === 'done').length;
        const st = (id, state, text) => ({ id, state, text });
        const out = [it.layout ? st('sketch', 'ok', '✓ Sketches') : st('sketch', 'skip', 'Sketches · not used'),
                     st('final', 'ok', '✓ Final')];
        if (it.review === 'checking') out.push(st('checks', 'now', 'Checking…'));
        else if (it.checks && it.checks.complete) out.push(st('checks', 'ok', '✓ Checks'));
        else if (it.checks) out.push(st('checks', 'warn', 'Checks · please look'));
        else if (checks.some(s => s.status === 'failed')) out.push(st('checks', 'warn', 'Checks · stopped'));
        else out.push(st('checks', 'wait', 'Checks · not run'));
        if (it.review === 'fixing') out.push(st('fixes', 'now', 'Fixing…'));
        else if (fixes.some(s => s.status === 'failed')) out.push(st('fixes', 'warn', 'Fixes · stopped'));
        else if (doneFixes) out.push(st('fixes', 'ok', '✓ Fixes · ' + doneFixes));
        else out.push(st('fixes', 'skip', 'Fixes · none'));
        if (it.review === 'approved' || it.review === 'anchor') out.push(st('you', 'ok', '✓ ' + this.reviewWord(it)));
        else if (it.review === 'rejected') out.push(st('you', 'bad', '✗ Rejected'));
        else if (BUSY_REVIEW.has(it.review)) out.push(st('you', 'wait', 'Your review'));
        else out.push(st('you', 'now', 'Your review'));
        out.push(st('export', 'wait', 'Export'));
        return out;
      },

      // --- review: the image large, before and after, and the checks drawn on it -------------------
      reviewThumbs(it) {  // the sketch it was built on, then the chain's images, oldest first
        const out = [];
        if (it.layout) {
          out.push({ key: 'sketch', path: it.layout, files: [it.layout], short: 'Sketch', latest: false,
                     long: 'The sketch it was built on (free, never published)' });
        }
        const images = Array.isArray(it.images) ? it.images : [];
        const made = chainFixes(it);  // the fix step that made each image, if any says so
        images.forEach((p, k) => {
          const latest = k === images.length - 1, many = images.length > 1;
          let short = many ? 'First' : 'Final', long = many ? 'The first image' : 'The final image';
          if (k > 0) {  // an image no step names stays neutral: never another step's name
            const s = made[k];
            short = (s && FIX_SHORT[s.kind]) || 'Image ' + (k + 1);
            long = (s && this.fixWords(s)) || 'Image ' + (k + 1);
          }
          out.push({ key: 'img' + k, path: p || null, files: p ? [p] : [], latest,
                     short: latest && many ? short + ' · latest' : short, long: latest && many ? long + ' (latest)' : long });
        });
        return out;
      },
      fixWords(s) {
        if (!s) return '';
        if (s.kind === 'repair') return 'After the repair of ' + (s.fault || 'one detail');
        if (s.kind === 'finish') return 'After the colour correction';
        if (s.kind === 'regenerate') return 'A new image of this shot';
        if (s.kind === 'composite') return 'With the real logo placed';
        return capital(s.label || '');
      },
      shownThumb(it) {  // the image shown large: the one picked below, else the latest
        const thumbs = this.reviewThumbs(it), v = this.review.view;
        return (v && v.id === it.id && thumbs.find(t => t.key === v.key)) || thumbs.find(t => t.latest) || null;
      },
      isShown(it, t) { const s = this.shownThumb(it); return !!s && s.key === t.key; },
      showImage(it, t) { this.review.view = { id: it.id, key: t.key }; },
      showingLatest(it) { const s = this.shownThumb(it); return !!s && s.latest; },
      bigFiles(it) {
        const s = this.shownThumb(it);
        return s && s.files.length && !this.review.broken[s.path] ? s.files : [];
      },
      imageMissing(it) { return !this.bigFiles(it).length; },
      bigAlt(it) { const s = this.shownThumb(it); return (s ? s.long + ': ' : '') + it.words; },
      shownLabel(it) {
        const s = this.shownThumb(it);
        if (!s) return '';
        const marks = !s.latest && it.checks ? ' · the check marks are on the latest image' : '';
        return 'Showing: ' + s.long + marks;
      },
      imageLoaded(path, e) {  // the image's own pixel size: the check boxes are in its pixels
        const img = e && e.target;
        if (img && img.naturalWidth && img.naturalHeight) this.review.sizes[path] = { w: img.naturalWidth, h: img.naturalHeight };
      },
      imageBroken(path) { this.review.broken[path] = true; },
      reviewBoxes(it) {  // the logo boxes the check found, on the latest image only; no box is ever guessed
        const size = it.latest && this.review.sizes[it.latest];
        if (!size || !this.showingLatest(it) || !it.checks) return [];
        const pct = (v, total) => Math.max(0, Math.min(100, (v / total) * 100));
        const out = [];
        (Array.isArray(it.checks.logo) ? it.checks.logo : []).forEach((l, i) => {
          const b = Array.isArray(l.box) ? l.box.map(Number) : [];
          if (b.length !== 4 || !b.every(Number.isFinite)) return;  // no box: the list says it instead
          const left = pct(Math.min(b[0], b[2]), size.w), top = pct(Math.min(b[1], b[3]), size.h);
          const width = Math.min(pct(Math.abs(b[2] - b[0]), size.w), 100 - left);
          const height = Math.min(pct(Math.abs(b[3] - b[1]), size.h), 100 - top);
          const where = l.placement ? capital(String(l.placement)) + ' logo' : 'Logo';
          out.push({ key: 'logo' + i, ok: !!l.ok, label: where + (l.ok ? ' ✓' : ' · please look'),
                     style: 'left:' + left + '%;top:' + top + '%;width:' + width + '%;height:' + height + '%' });
        });
        return out;
      },
      colourBadge(it) {  // the colour result on the latest image, e.g. "Colour difference 2.1 (limit 5)"
        const c = it.checks && it.checks.colour;
        if (!c || !this.showingLatest(it) || !this.bigFiles(it).length) return '';
        return c.difference === null || c.difference === undefined ? 'Colour not measured' : String(c.words || '');
      },
      colourBadgeOk(it) { const c = it.checks && it.checks.colour; return !!c && c.ok === true; },

      // --- review: checks in plain words -------------------------------------------------------------
      checkLines(it) {  // pass ✓, fixed ↻, a problem ✗, and "Not checked — please look" for a line nobody judged
        const c = it.checks;
        if (!c) return [];
        const out = [];
        const add = (key, state, text, sub, note) => out.push({ key, state, mark: CHECK_MARKS[state], sr: CHECK_SR[state],
                                                               text: String(text || ''), sub: sub || '', note: note || '' });
        const col = c.colour;
        if (col) {
          if (col.ok === null || col.ok === undefined) add('colour', 'look', col.words, NOT_CHECKED);
          else add('colour', col.ok ? 'ok' : 'bad', col.ok ? 'Colour matches' : 'Colour is off', col.words);
        }
        if (c.text) add('text', c.text.ok ? 'ok' : 'bad', c.text.words);
        (Array.isArray(c.logo) ? c.logo : []).forEach((l, i) => add('logo' + i, l.ok ? 'ok' : 'bad', l.words));
        (Array.isArray(c.fixed) ? c.fixed : []).forEach((f, i) =>
          add('fixed' + i, 'fix', 'Fixed: ' + f, 'Fixed automatically · compare before and after'));
        if (c.visual_error) add('visual', 'look', c.visual_error, NOT_CHECKED);
        (Array.isArray(c.lines) ? c.lines : []).forEach((l, i) => {
          if (l.ok === true) add('line' + i, 'ok', l.text, '', l.note);
          else if (l.ok === false) add('line' + i, 'bad', l.text, l.fault ? 'Problem: ' + l.fault : 'A problem was found', l.note);
          else add('line' + i, 'look', l.text, NOT_CHECKED, l.note);
        });
        return out;
      },
      checksIncomplete(it) {  // why the checks are not a verdict, unless the "needs a person" line says it already
        const c = it.checks;
        return c && !c.complete && c.reason && !(it.review === 'needs_person' && it.reason === c.reason) ? c.reason : '';
      },
      noChecksText(it) { return it.review === 'checking' ? REVIEW_NOTES.checking : REVIEW_NOTES.notChecked; },
      runningText(it) {  // the step in progress, in plain words
        const s = (Array.isArray(it.steps) ? it.steps : []).find(x => BUSY_STEP.has(x.status));
        if (!s) return BUSY_REVIEW.has(it.review) ? REVIEW_NOTES.checking : '';
        const what = s.kind === 'check' ? 'the checks' : capital(s.label || '') + (s.fault ? ' of ' + s.fault : '');
        return (s.status === 'queued' ? 'Waiting: ' : 'Running: ') + what + '…';
      },
      spendText(it) { return 'Spend so far ' + smallMoney(it.cost_usd) + ' for this image'; },
      limitsText(it) {  // while the image is still open to fixes
        const l = plainObject(it.limits_left);
        if (!this.isOpen(it) && !busyReview(it)) return '';
        if (!Number.isInteger(l.repairs) || !Number.isInteger(l.regenerations)) return '';
        return 'Repairs left on this image: ' + l.repairs + ' · new images left for this shot: ' + l.regenerations;
      },
      stepLines(it) {
        return (Array.isArray(it.steps) ? it.steps : []).map(s => ({
          key: s.id, text: capital(s.label || '') + (s.fault ? ': ' + s.fault : ''),
          meta: [STEP_STATUS[s.status] || '', s.cost_usd ? smallMoney(s.cost_usd) : ''].filter(Boolean).join(' · '),
          error: s.error || '' }));
      },
      toggleSteps() { this.review.stepsOpen = !this.review.stepsOpen; },

      // --- review: a person's verdicts and requests (one request at a time) ---------------------------
      isOpen(it) { const a = plainObject(it.actions); return OPEN_ACTIONS.some(k => a[k] === true); },
      reviewMsg(it) { const m = this.review.msg; return m && m.id === it.id ? m : { text: '', err: false }; },
      async reviewAct(it, what, body) {  // POST /api/reviews/<id>/<what>; the reply's item replaces this one
        if (this.review.busy !== null) return null;
        const id = it.id;
        Object.assign(this.review, { busy: id, msg: null });
        let res, data;
        try {
          res = await fetch('/api/reviews/' + id + '/' + what, post(body));
          data = plainObject(await res.json().catch(() => null));
        } catch (e) {
          this.review.msg = { id, err: true, text: REVIEW_NOTES.unreachable };
          return null;
        } finally {
          this.review.busy = null;
        }
        if (res.ok && data.item) {
          this.putReview(data.item, false);
          return data;
        }
        if (what === 'anchor' && res.status === 409 && data.anchor_exists === true) {  // Replace is a second click
          this.openForm(it, 'anchor', { note: String(body.note || ''), replace: true });
          this.refreshReview(id);
          return null;
        }
        const said = typeof data.error === 'string' ? capital(data.error.trim()) : '';
        this.review.msg = { id, err: true, text: !said ? REVIEW_NOTES.refused : /[.!?]$/.test(said) ? said : said + '.' };
        if (res.status === 409 || res.status === 404) this.refreshReview(id);  // e.g. already decided elsewhere
        return null;
      },
      async sendVerdict(it, what, body, done) {
        const data = await this.reviewAct(it, what, body);
        if (!data) return null;
        this.review.form = null;
        this.review.msg = { id: it.id, err: false, text: done };
        return data;
      },
      approveReview(it) {  // from "needs a person" the server asks for a note saying why it is fine
        if (plainObject(it.actions).note_required) { this.openForm(it, 'approve'); return null; }
        return this.sendVerdict(it, 'approve', { note: '' }, REVIEW_NOTES.approved);
      },
      anchorReview(it) {
        const a = plainObject(it.actions);
        if (a.note_required || a.anchor_exists) {
          this.openForm(it, 'anchor', { replace: !!a.anchor_exists });
          return null;
        }
        return this.sendVerdict(it, 'anchor', { note: '', replace: false }, REVIEW_NOTES.anchor);
      },
      openForm(it, kind, extra) {  // from: the button the keyboard goes back to on Cancel
        this.review.picker = null;
        this.review.msg = null;
        this.review.form = { id: it.id, kind, note: '', replace: false, from: 'rv-' + kind + '-' + it.id, ...extra };
        window.Alpine.nextTick(() => {  // the keyboard goes to the note, else to the confirm button
          const el = document.querySelector('#rv-form textarea') || document.querySelector('#rv-form button[type="submit"]');
          if (el) el.focus();
        });
      },
      closeForm(giveBack) {
        const f = this.review.form;
        this.review.form = null;
        if (!giveBack || !f || !f.from) return;
        window.Alpine.nextTick(() => { const b = document.getElementById(f.from); if (b) b.focus(); });
      },
      formFor(it) {  // the open form, only while the server still allows what it asks for
        const f = this.review.form, a = plainObject(it.actions);
        return f && f.id === it.id && a[f.kind] === true ? f : null;
      },
      formNeedsNote(it) {
        const f = this.formFor(it);
        return !!f && (f.kind === 'reject' || plainObject(it.actions).note_required === true);
      },
      formQuestion(it) { const f = this.formFor(it); return f && f.kind === 'anchor' && f.replace ? REVIEW_NOTES.replace : ''; },
      formLabel(it) {
        const f = this.formFor(it);
        if (!f) return '';
        if (f.kind === 'reject') return 'What is wrong? Your note becomes a draft rule for future images.';
        return 'This image needs a person. Say why it is fine:';
      },
      formButton(it) {
        const f = this.formFor(it);
        if (!f) return '';
        if (f.kind === 'reject') return 'Reject';
        if (f.kind === 'anchor') return f.replace ? 'Replace' : 'Approve as reference packshot';
        return 'Approve';
      },
      formNote() { return this.review.form ? this.review.form.note : ''; },
      setFormNote(v) { if (this.review.form) this.review.form.note = String(v); },
      canSubmitForm(it) {
        const f = this.formFor(it);
        return !!f && this.review.busy === null && (!this.formNeedsNote(it) || !!f.note.trim());
      },
      submitForm(it) {
        if (!this.canSubmitForm(it)) return null;
        const f = this.review.form, note = f.note.trim();
        if (f.kind === 'reject') return this.sendReject(it, note);
        if (f.kind === 'anchor') return this.sendVerdict(it, 'anchor', { note, replace: !!f.replace }, REVIEW_NOTES.anchor);
        return this.sendVerdict(it, 'approve', { note }, REVIEW_NOTES.approved);
      },
      async sendReject(it, note) {
        const data = await this.sendVerdict(it, 'reject', { note }, REVIEW_NOTES.rejected);
        const draft = data && data.rule && typeof data.rule.prompt_text === 'string' ? data.rule.prompt_text.trim() : '';
        if (draft) this.review.msg.text += ' Draft: “' + draft + '”';
        if (data) this.loadRules();  // its pending rule, in the rules panel
        return data;
      },
      isApproved(it) { return it.review === 'approved' || it.review === 'anchor'; },
      openInLibrary(id) {  // "Open in Library" on an approved image: its record, where it is exported
        Object.assign(this.library, { selected: id, msg: null, copied: null, promptOpen: false });
        this.openTab(TABS.find(t => t.id === 'library'));
      },
      togglePicker(it) {
        Object.assign(this.review, { form: null, msg: null, picker: this.review.picker === it.id ? null : it.id });
      },
      pickerOpen(it) { return this.review.picker === it.id && this.isOpen(it); },
      changeHint(it) {
        const n = plainObject(it.limits_left).repairs;
        return 'Each fix edits this image once, within its limits' + (Number.isInteger(n) ? ' (repairs left: ' + n + ').' : '.');
      },
      noChangeText(it) {  // why no fix can be asked for, in plain words (the server decides; this only explains)
        if (busyReview(it)) return REVIEW_NOTES.running;
        if (!this.isOpen(it)) return REVIEW_NOTES.decided;
        return plainObject(it.limits_left).repairs === 0 ? REVIEW_NOTES.noRepairs : REVIEW_NOTES.noChanges;
      },
      async askChange(it, c) {
        const data = await this.reviewAct(it, 'change', { code: c.code });
        if (!data) return null;
        this.review.picker = null;
        this.review.msg = { id: it.id, err: false, text: 'Asked for: ' + c.label + '. The fix is running; this page updates by itself.' };
        return data;
      },
      async runChecks(it) {
        const data = await this.reviewAct(it, 'check', {});
        if (data) this.review.msg = { id: it.id, err: false, text: REVIEW_NOTES.checksStarted };
        return data;
      },
      openInBuilder(it) {  // "Change my choices": Create image with this image's answers, through the usual flow
        const a = clone(plainObject(it.answers));
        for (const k of new Set([...Object.keys(this.answers), ...Object.keys(a)])) edits[k] = (edits[k] || 0) + 1;
        touchSeq++;
        briefJump = null;
        this.answers = a;
        const c = a.colour;
        Object.assign(this.builder.custom, c && typeof c === 'object'
          ? { open: true, name: String(c.name || ''), hex: String(c.hex || ''), hint: '' }
          : { open: false, name: '', hex: '', hint: '' });
        Object.assign(this.brief, { done: false, picked: {}, owned: {}, hint: '', error: '' });
        Object.assign(this.builder, { active: null, showAll: false, fresh: [], byWords: [] });
        this.answersChanged();
        this.saveDraft();
        this.askQuestions();
        Object.assign(this.ui, { tab: 'create', note: REVIEW_NOTES.builder });
        this.review.picker = null;
        window.Alpine.nextTick(() => window.requestAnimationFrame(() => {
          window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
          const card = document.getElementById('summary');
          if (shown(card)) card.focus({ preventScroll: true });
        }));
      },

      // --- prompt rules: the pending rules panel in the Review tab ------------------------------------
      async loadRules() {
        const seq = ++rulesSeq, since = rulesEpoch;
        let res;
        try {
          res = await api('/api/rules');
        } catch (e) {
          if (seq === rulesSeq) this.rules.error = RULE_NOTES.list;
          return;
        }
        if (seq !== rulesSeq) return;  // a newer list is on its way
        const old = new Map(this.rules.items.map(r => [r.index, r]));
        const items = (Array.isArray(res.rules) ? res.rules : []).filter(r => r && Number.isInteger(r.index))
          .map(r => ((rulesActed.get(r.index) || 0) > since && old.has(r.index) ? old.get(r.index) : r));
        const scopes = (Array.isArray(res.scopes) ? res.scopes : [])
          .filter(s => s && typeof s.value === 'string' && s.value);
        Object.assign(this.rules, { items, scopes, loaded: true, error: '' });
        items.forEach(r => this.syncRuleEdit(r));
      },
      syncRuleEdit(r) {  // the field starts as the AI's draft; what the person typed is kept when the list reloads
        if (r.status !== 'pending') { delete this.rules.edits[r.index]; return; }
        const draft = typeof r.prompt_text === 'string' ? r.prompt_text : '';
        const scope = this.knownScope(r.applies_to);
        const e = this.rules.edits[r.index];
        if (!e) { this.rules.edits[r.index] = { text: draft, scope, draft, draftScope: scope }; return; }
        if (e.text === e.draft) e.text = draft;  // left alone: a newer draft replaces it
        if (e.scope === e.draftScope) e.scope = scope;
        Object.assign(e, { draft, draftScope: scope });
      },
      knownScope(v) { return this.rules.scopes.some(s => s.value === v) ? v : ''; },
      putRule(rule) {  // an action's reply replaces that rule
        rulesActed.set(rule.index, ++rulesEpoch);
        const i = this.rules.items.findIndex(r => r.index === rule.index);
        if (i >= 0) this.rules.items.splice(i, 1, rule);
        else this.rules.items.push(rule);
        this.syncRuleEdit(rule);
      },
      pendingRules() { return this.rules.items.filter(r => r.status === 'pending'); },
      activeRules() { return this.rules.items.filter(r => r.status === 'active'); },
      dismissedText() {
        const n = this.rules.items.filter(r => r.status === 'dismissed').length;
        return n ? n + (n === 1 ? ' rule was' : ' rules were') + ' dismissed and will not be used.' : '';
      },
      rulesBadge() { const n = this.pendingRules().length; return n ? n + ' waiting' : ''; },
      scopeLabel(v) { const s = this.rules.scopes.find(x => x.value === v); return s ? s.label : String(v || ''); },
      ruleEdit(r) { return this.rules.edits[r.index] || { text: '', scope: '', draft: '', draftScope: '' }; },
      ruleText(r) { return this.ruleEdit(r).text; },
      setRuleText(r, v) {
        const e = this.rules.edits[r.index];
        if (e) e.text = String(v);
        this.clearRuleMsg(r);
      },
      setRuleScope(r, v) {
        const e = this.rules.edits[r.index];
        if (e) e.scope = this.knownScope(v);
        this.clearRuleMsg(r);
      },
      clearRuleMsg(r) { const m = this.rules.msg; if (m && m.index === r.index) this.rules.msg = null; },
      isScope(r, v) { return this.ruleEdit(r).scope === v; },
      ruleWords(r) { return wordCount(this.ruleText(r)); },
      ruleTooLong(r) { return this.ruleWords(r) > RULE_WORDS_MAX; },
      ruleCountText(r) {
        const n = this.ruleWords(r);
        if (!n) return 'Write the rule as one sentence, up to ' + RULE_WORDS_MAX + ' words.';
        if (n > RULE_WORDS_MAX) return n + ' words: keep it to ' + RULE_WORDS_MAX + ' or fewer.';
        return n + ' of ' + RULE_WORDS_MAX + ' words';
      },
      ruleNoDraft(r) { return r.status === 'pending' && !String(r.prompt_text || '').trim() ? RULE_NOTES.noDraft : ''; },
      ruleSource(r) {  // where the note came from: the rejected image, in plain words, when it is in the review list
        const it = this.review.items.find(i => r.from_shot && i.shot_id === r.from_shot);
        const day = new Date(String(r.date || '') + 'T12:00:00');  // a date only: shown as a day, never a time
        const when = Number.isNaN(day.getTime()) ? '' : day.toLocaleDateString([], { day: 'numeric', month: 'short' });
        return ['Note from a rejection', it ? it.words : '', when].filter(Boolean).join(' · ');
      },
      ruleBusy(r, what) { const b = this.rules.busy; return !!b && b.index === r.index && (!what || b.what === what); },
      canSwitchOn(r) {
        const n = this.ruleWords(r);
        return this.rules.busy === null && r.status === 'pending' && n > 0 && n <= RULE_WORDS_MAX
          && !!this.knownScope(this.ruleEdit(r).scope);
      },
      switchOnLabel(r) { return this.ruleBusy(r, 'activate') ? 'Switching on…' : 'Switch on'; },
      ruleMsg(r) { const m = this.rules.msg; return m && m.index === r.index ? m : { text: '', err: false }; },
      panelMsg() {  // an answer about a rule no longer waiting (switched on, dismissed, decided elsewhere)
        const m = this.rules.msg;
        if (!m || this.pendingRules().some(r => r.index === m.index)) return { text: '', err: false };
        const r = this.rules.items.find(x => x.index === m.index);  // a refusal says which rule it is about
        return m.err && r && r.note ? { err: true, text: 'The rule from “' + r.note + '”: ' + m.text } : m;
      },
      async ruleAct(r, what, body) {  // POST /api/rules/<index>/<what>; the reply's rule replaces this one
        if (this.rules.busy !== null) return null;
        const index = r.index;
        Object.assign(this.rules, { busy: { index, what }, msg: null });
        let res, data;
        try {
          res = await fetch('/api/rules/' + index + '/' + what, post(body));
          data = plainObject(await res.json().catch(() => null));
        } catch (e) {
          this.rules.msg = { index, err: true, text: RULE_NOTES.unreachable };
          return null;
        } finally {
          this.rules.busy = null;
        }
        const rule = plainObject(data.rule);
        if (res.ok && Number.isInteger(rule.index)) {
          this.putRule(rule);
          return rule;
        }
        // 409 too long or decided elsewhere, 422 words or scope, 404 gone: the server's own words, in place
        const said = typeof data.error === 'string' ? capital(data.error.trim()) : '';
        this.rules.msg = { index, err: true, text: !said ? RULE_NOTES.refused : /[.!?]$/.test(said) ? said : said + '.' };
        if (res.status === 409 || res.status === 404) this.loadRules();  // switched on or dismissed elsewhere
        return null;
      },
      async switchOnRule(r) {  // the only way a rule is switched on: this button, pressed by a person
        if (!this.canSwitchOn(r)) return null;
        const e = this.ruleEdit(r), scope = e.scope;
        this.rules.ask = null;
        const rule = await this.ruleAct(r, 'activate', { prompt_text: e.text.trim(), applies_to: scope });
        if (!rule) return null;
        this.rules.msg = { index: rule.index, err: false, text: RULE_NOTES.on + this.scopeLabel(rule.applies_to).toLowerCase() + '.' };
        this.focusRules();
        return rule;
      },
      askDismiss(r) {  // Dismiss asks in place first
        if (this.rules.busy !== null) return;
        this.rules.ask = r.index;
        this.clearRuleMsg(r);
        window.Alpine.nextTick(() => { const b = document.getElementById('rule-dismiss-yes'); if (b) b.focus(); });
      },
      cancelDismiss(r) {
        this.rules.ask = null;
        window.Alpine.nextTick(() => { const b = document.getElementById('rule-dismiss-' + r.index); if (b) b.focus(); });
      },
      dismissOpen(r) { return this.rules.ask === r.index; },
      async dismissRule(r) {
        if (this.rules.ask !== r.index) return null;
        const rule = await this.ruleAct(r, 'dismiss', {});
        this.rules.ask = null;
        if (!rule) return null;
        this.rules.msg = { index: rule.index, err: false, text: RULE_NOTES.dismissed };
        this.focusRules();
        return rule;
      },
      focusRules() {  // after a rule leaves the waiting list, the keyboard goes to the panel's heading
        window.Alpine.nextTick(() => { const h = document.getElementById('rules-title'); if (h) h.focus(); });
      },
      jumpToRules() {
        const h = document.getElementById('rules-title');
        if (!h) return;
        h.scrollIntoView({ behavior: scrollMode(), block: 'start' });
        h.focus({ preventScroll: true });
      },
      activeRuleMeta(r) {
        return ['For ' + this.scopeLabel(r.applies_to).toLowerCase(), r.activated_by ? 'switched on by ' + r.activated_by : '']
          .filter(Boolean).join(' · ');
      },

      // --- library: approved images, their record and the web export ----------------------------------
      async loadLibrary() {
        const seq = ++librarySeq, since = libraryEpoch;
        let res;
        try {
          res = await api('/api/library');
        } catch (e) {
          if (seq === librarySeq) this.library.error = LIBRARY_NOTES.list;
          return;
        }
        if (seq !== librarySeq) return;  // a newer list is on its way
        this.library.note = '';
        const old = new Map(this.library.items.map(i => [i.id, i]));
        const items = (Array.isArray(res.items) ? res.items : []).filter(i => i && Number.isInteger(i.id))
          .map(i => ((libraryActed.get(i.id) || 0) > since && old.has(i.id) ? { ...i, export: old.get(i.id).export } : i));
        for (const i of items) {  // an image that changed may load now: it is tried again
          const was = old.get(i.id);
          if (was && was.image !== i.image && this.library.broken[i.image]) delete this.library.broken[i.image];
        }
        Object.assign(this.library, { items, facets: plainObject(res.facets), loaded: true, error: '' });
        const f = this.library.filters;
        for (const { key } of FILTERS) {  // a filter value no longer present is cleared, never left matching nothing
          if (f[key] && !this.facetValues(key).some(v => v.value === f[key])) f[key] = '';
        }
        if (this.library.selected !== null && !items.some(i => i.id === this.library.selected)) {
          // the record open in the drawer is no longer approved (rejected since, or its file is gone): say so
          if (this.library.loaded && old.has(this.library.selected)) this.library.note = LIBRARY_NOTES.left;
          this.library.selected = null;
        }
      },
      libraryRetry() { this.library.broken = {}; this.loadLibrary(); },
      libraryNote(key) { return LIBRARY_NOTES[key] || ''; },
      facetValues(key) {
        const list = this.library.facets[key];
        return (Array.isArray(list) ? list : []).filter(v => v && typeof v.value === 'string' && v.value)
          .map(v => ({ value: v.value, label: capital(String(v.label || v.value)) }));
      },
      shownFilters() { return FILTERS.filter(f => this.facetValues(f.key).length); },
      isFilter(key, v) { return (this.library.filters[key] || '') === v; },
      setFilter(key, v) {
        if (!(key in this.library.filters)) return;
        this.library.filters[key] = this.facetValues(key).some(x => x.value === v) ? v : '';
      },
      anyFilter() { return FILTERS.some(f => this.library.filters[f.key]); },
      clearFilters() { for (const f of FILTERS) this.library.filters[f.key] = ''; },
      shownLibrary() {
        const f = this.library.filters;
        return this.library.items.filter(i => FILTERS.every(({ key }) => !f[key] || i[key] === f[key]));
      },
      libraryCount() {
        const all = this.library.items.length, n = this.shownLibrary().length;
        if (!all) return '';
        const word = c => c + (c === 1 ? ' approved image' : ' approved images');
        return this.anyFilter() ? 'Showing ' + n + ' of ' + word(all) : word(all);
      },
      libraryEmpty() {
        if (!this.library.loaded || this.library.error) return '';
        if (!this.library.items.length) return LIBRARY_NOTES.empty;
        return this.shownLibrary().length ? '' : LIBRARY_NOTES.none;
      },
      facetLabel(key, v) { const x = this.facetValues(key).find(o => o.value === v); return x ? x.label : capital(String(v || '')); },
      tileTitle(it) {  // "Olive · Oversized Pullover Hoodie"
        return [it.colour_name || this.facetLabel('colour', it.colour), this.facetLabel('style', it.style)].filter(Boolean).join(' · ');
      },
      tileSub(it) {  // "On model · Maya" / "Packshot", and the decoration: "3D puff embroidery · centre chest"
        const kind = [KIND_WORDS[it.kind] || '', it.model || ''].filter(Boolean).join(' · ');
        const deco = [capital(it.technique || ''), it.placement || ''].filter(Boolean).join(' · ');
        return [kind, deco].filter(Boolean).join(' — ');
      },
      libraryHex(it) { return this.jobHex({ answers: { colour: it.colour } }); },
      libraryThumb(it) { return it.image && !this.library.broken[it.image] ? [it.image] : []; },  // keyed: x-bind binds once
      libraryBroken(path) { this.library.broken[path] = true; },
      selectedRecord() { return this.library.items.find(i => i.id === this.library.selected) || null; },
      selectedRecords() { const it = this.selectedRecord(); return it ? [it] : []; },  // x-for over it names the item
      isRecord(it) { return this.library.selected === it.id; },
      openRecord(it) {
        Object.assign(this.library, { selected: it.id, msg: null, copied: null, promptOpen: false, note: '' });
        window.Alpine.nextTick(() => {
          const h = document.getElementById('lib-rec-title');
          if (!h) return;
          h.scrollIntoView({ behavior: scrollMode(), block: 'nearest' });
          h.focus({ preventScroll: true });
        });
      },
      closeRecord() {
        const id = this.library.selected;
        Object.assign(this.library, { selected: null, msg: null, copied: null, promptOpen: false });
        window.Alpine.nextTick(() => { const b = document.getElementById('lib-tile-' + id); if (b) b.focus(); });
      },
      recordRows(it) {  // the record in plain words
        const rows = [
          { k: 'Shot', v: it.words },
          { k: 'For', v: PURPOSE_WORDS[it.purpose] || '' },
          { k: 'Approved by', v: [it.approved_by, dateTime(it.approved_at)].filter(Boolean).join(' · ') },
          { k: 'Cost so far', v: smallMoney(it.cost_usd) },
          { k: 'AI model', v: typeof it.ai_model_label === 'string' ? it.ai_model_label : '' },  // the server's words
        ];
        return rows.filter(r => r.v);
      },
      toggleLibraryPrompt() { this.library.promptOpen = !this.library.promptOpen; },
      promptText(it) { return typeof it.prompt === 'string' && it.prompt ? it.prompt : LIBRARY_NOTES.noPrompt; },
      isClient(it) { return it.purpose === 'client'; },
      exportOf(it) { return it.export && typeof it.export === 'object' ? it.export : null; },
      exportFiles(it) {  // the WebP sizes, widest first: "1600 px wide"
        const ex = this.exportOf(it);
        return (ex && Array.isArray(ex.files) ? ex.files : []).filter(f => f && typeof f.path === 'string' && f.path)
          .map(f => ({ path: f.path, text: (Number.isInteger(f.width) ? f.width + ' px wide' : fileName(f.path)) + ' · WebP' }));
      },
      exportPdf(it) { const ex = this.exportOf(it); return ex && typeof ex.pdf === 'string' && ex.pdf ? [ex.pdf] : []; },
      fileLink(path) { return { href: fileUrl(path), target: '_blank', rel: 'noopener noreferrer' }; },  // x-bind object
      pdfLink(path) { return { href: fileUrl(path), download: fileName(path) }; },  // the server sends it as an attachment
      exporting(it, pdf) { const b = this.library.busy; return !!b && b.id === it.id && (pdf === undefined || b.pdf === pdf); },
      exportLabel(it) {
        if (this.exporting(it, false)) return 'Exporting…';
        return this.exportOf(it) ? 'Export for the website again' : 'Export for the website';
      },
      pdfLabel(it) {
        if (this.exporting(it, true)) return 'Making the approval PDF…';
        return this.exportPdf(it).length ? 'Approval PDF again' : 'Approval PDF';
      },
      libraryMsg(it) { const m = this.library.msg; return m && m.id === it.id ? m : { text: '', err: false }; },
      async exportRecord(it, pdf) {  // POST /api/library/<id>/export: free, no AI call; one at a time
        if (this.library.busy !== null) return null;
        const id = it.id;
        Object.assign(this.library, { busy: { id, pdf: !!pdf }, msg: null, copied: null });
        let res, data;
        try {
          res = await fetch('/api/library/' + id + '/export', post({ pdf: !!pdf }));
          data = await res.json().catch(() => null);
        } catch (e) {
          this.library.msg = { id, err: true, text: LIBRARY_NOTES.unreachable };
          return null;
        } finally {
          this.library.busy = null;
        }
        if (res.ok && data && typeof data === 'object' && Array.isArray(data.files)) {
          libraryActed.set(id, ++libraryEpoch);
          const item = this.library.items.find(i => i.id === id);
          if (item) item.export = data;
          this.library.msg = { id, err: false, text: pdf ? LIBRARY_NOTES.pdf : LIBRARY_NOTES.exported };
          return data;
        }
        const said = typeof plainObject(data).error === 'string' ? capital(data.error.trim()) : '';
        let text = res.status === 404 ? LIBRARY_NOTES.gone : said || LIBRARY_NOTES.refused;
        if (!/[.!?]$/.test(text)) text += '.';
        this.library.msg = { id, err: true, text };
        if (res.status === 404 || res.status === 409) this.loadLibrary();  // e.g. rejected since, or the file is gone
        return null;
      },
      copyField(it, field) {  // the clipboard is written inside the click itself; if it refuses, the text is selected
        const ex = this.exportOf(it);
        const text = ex && typeof ex[field] === 'string' ? ex[field] : '';
        const el = document.getElementById('lib-' + field + '-' + it.id);
        const what = field === 'alt' ? 'Alt text' : 'Caption';
        const answer = ok => {
          clearTimeout(copyTimer);
          this.library.copied = { id: it.id, field, ok };
          this.library.announce = ok ? what + ' copied.' : what + ' selected: press Ctrl+C to copy it.';
          copyTimer = setTimeout(() => { copyTimer = null; this.library.copied = null; }, COPY_MS);
        };
        const fallback = () => { selectText(el); answer(false); };
        let done = null;
        try {
          if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') done = navigator.clipboard.writeText(text);
        } catch (e) {
          done = null;
        }
        if (!done || typeof done.then !== 'function') { fallback(); return; }
        done.then(() => answer(true), fallback);
      },
      copyLabel(it, field) {
        const c = this.library.copied;
        if (!c || c.id !== it.id || c.field !== field) return 'Copy';
        return c.ok ? 'Copied ✓' : 'Press Ctrl+C';
      },
    };
  }

  document.addEventListener('alpine:init', () => {
    Alpine.store('studio', createStudio());  // Alpine runs the store's init() itself, once
  });
})();
