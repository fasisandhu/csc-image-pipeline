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
    { id: 'review', label: 'Review', later: true },
    { id: 'library', label: 'Library', later: true },
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
    otherShot: 'These sketches are for other choices than the ones above, so they cannot be picked. Make new sketches for this shot.',
    failed: 'Nothing was retried. You can try again with the buttons above.',
    noError: 'It stopped without saying why.',
    interrupted: 'Stopped when the studio restarted; nothing was retried.',
    review: 'Checks and approval come in the Review tab (next step).',
    anchorDone: 'Approve this reference packshot in the Review tab (next step); then come back and create your shot.',
    unreachable: "Couldn't reach the studio; check it is running, then try again.",
    refused: "The studio couldn't start this. Try again in a moment.",
    list: "Couldn't load your recent images; check the studio is running.",
    lost: 'Lost touch with the studio while checking on your images. Check it is running, then check again.',
    unchecked: 'Not being checked: lost touch with the studio (see Recent images).',
    changed: 'Your choices changed — check the cost again.',
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
      // ui: current tab, guide panel, tour (task 8)
      ui: { tabs: TABS, tab: 'create', note: '', guideOpen: false, tourSeen: storage.get('tourSeen', false), tour: null,
            promptOpen: false },

      init() {
        this.refreshBudget();
        setInterval(() => this.refreshBudget(), BUDGET_REFRESH_MS);
        const c = this.answers.colour;
        if (c && typeof c === 'object') {
          Object.assign(this.builder.custom, { open: true, name: String(c.name || ''), hex: String(c.hex || '') });
        }
        this.loadQuestions();
        this.loadJobs();
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
      },
      toggleGuide() { this.ui.guideOpen = !this.ui.guideOpen; },
      saveDraft() { storage.set('answers', this.answers); },
      markTourSeen() { this.ui.tourSeen = true; storage.set('tourSeen', true); },

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
        if (ACTIVE.has(was) && !ACTIVE.has(job.status)) this.refreshBudget();  // it may have spent
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
      layout() {  // the chosen sketch, only while it belongs to the shot on screen
        const c = this.chosen;
        return c && c.shotId === this.currentShot() ? c.file : null;
      },
      layoutNote() { return this.layout() ? 'Builds on sketch ' + this.chosen.n + ', your chosen layout.' : ''; },
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
      canChoose(j) { return j.kind === 'drafts' && j.status === 'done' && !!j.shot_id && j.shot_id === this.currentShot(); },
      isChosen(f) { return !!this.chosen && this.chosen.file === f; },
      choose(j, f, i) {  // a second click on the chosen sketch un-picks it
        if (!this.canChoose(j)) return;
        this.run.confirm = null;  // a confirm was for the old layout
        this.chosen = this.isChosen(f) ? null
          : { file: f, n: i + 1, shotId: j.shot_id, caption: j.caption || '', words: this.jobWords(j) };
      },
      sketchAlt(j, i) { return 'Sketch ' + (i + 1) + ' of ' + this.jobWords(j); },
      sketchHint(j) { return this.canChoose(j) ? JOB_NOTES.pick : JOB_NOTES.otherShot; },
      finalNote(j) { return this.isAnchor(j) ? JOB_NOTES.anchorDone : JOB_NOTES.review; },
      shownImage() {  // the big preview box: the watched job's finished final, else the chosen sketch
        const w = this.watchedJob();
        if (w && w.kind === 'final' && w.status === 'done' && jobFiles(w).length) {
          return { file: jobFiles(w)[0], alt: (this.isAnchor(w) ? 'Reference packshot: ' : 'Final image: ') + this.jobWords(w),
                   label: this.jobKind(w), caption: w.caption || '', note: this.finalNote(w) };
        }
        const c = this.chosen;
        if (c) {
          return { file: c.file, alt: 'Sketch ' + c.n + ' of ' + c.words, caption: c.caption,
                   label: 'Sketch ' + c.n + ' · your chosen layout · free, never published', note: '' };
        }
        return null;
      },
    };
  }

  document.addEventListener('alpine:init', () => {
    Alpine.store('studio', createStudio());
    Alpine.store('studio').init();
  });
})();
