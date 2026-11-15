// CSC Studio page: one Alpine store, organised by concern (storage, api, builder rules, then the
// store: budget, tabs, builder questions, answers, preview, fill from my words and the summary).
// Later tasks add the jobs.
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

  function plainObject(v) { return v && typeof v === 'object' && !Array.isArray(v) ? v : {}; }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function money(n) { return '$' + Number(n).toFixed(2); }
  function capital(t) { return t ? t.charAt(0).toUpperCase() + t.slice(1) : t; }
  function reducedMotion() {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
  }

  // timers and request numbers: a reply is used only if no newer request was made since.
  // questionsOpen / previewOpen: the number of the newest request still in flight (0 if none).
  let questionsTimer = null, questionsSeq = 0, questionsOpen = 0;
  let previewTimer = null, previewSeq = 0, previewOpen = 0;
  let wordsReveal = false;  // the next questions reply shows sections opened by "Fill from my words"
  const edits = {};  // section id -> how often the person set it: a brief reply never overrides a newer edit

  // --- store ----------------------------------------------------------------------------------
  function createStudio() {
    return {
      // answers: what the person picked, by section id (hidden sections keep theirs for later)
      answers: plainObject(storage.get('answers', {})),
      // sections / missing: the question list from /api/questions, in server order
      sections: [],
      missing: [],
      // builder: page state of the sections (NEW badges, open tips, custom colour fields)
      builder: {
        loaded: false, error: '', previewError: '', announce: '',
        seen: null, fresh: [], byWords: [], tips: {},
        custom: { open: false, name: '', hex: '', hint: '' },  // hint: a colour word from the brief
        waiting: false,  // answers changed and the options or cost are not back yet
      },
      // brief: the "What do you want to make?" box. picked: {section id: value} the last words filled in;
      // owned: every value words filled in this visit (new words may replace those the person left alone)
      brief: { text: '', busy: false, error: '', done: false, picked: {}, owned: {}, hint: '', provider: '', fallback: false },
      // preview: prompt, cost and route tier for complete answers from /api/preview
      preview: null,
      // job / jobs: the job being watched and the job list (task 7)
      job: null,
      jobs: [],
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
          }
          return;
        } finally {
          if (questionsOpen === seq) questionsOpen = 0;
          this.syncWaiting();
        }
        if (seq !== questionsSeq || questionsTimer) return;  // newer answers are on their way
        this.builder.error = '';
        this.applySections(res, sent);
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
        let dropped = false;
        for (const k of Object.keys(sent)) {
          if (!kept.has(k) && same(this.answers[k], sent[k])) { delete this.answers[k]; dropped = true; }
        }
        if (dropped) this.saveDraft();
        // NEW badges: sections that were hidden on the last reply; kept until answered or hidden again
        const b = this.builder;
        const visible = res.sections.filter(s => s.visible).map(s => s.id);
        const added = b.seen ? visible.filter(id => !b.seen.includes(id)) : [];
        b.fresh = b.fresh.filter(id => visible.includes(id)).concat(added);
        b.byWords = b.byWords.filter(id => b.fresh.includes(id)).concat(wordsReveal ? added : []);
        wordsReveal = false;
        b.seen = visible;
        if (added.length) {
          const titles = res.sections.filter(s => added.includes(s.id)).map(s => s.title);
          b.announce = 'New questions: ' + titles.join(', ') + '.';
        }
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

      // --- builder: answers -----------------------------------------------------------------
      setAnswer(id, value) {  // every pick, custom colour edit and size change by the person comes here
        if (value === null || value === undefined) delete this.answers[id];
        else this.answers[id] = value;
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
        this.builder.custom.open = true;
        this.applyCustom();
      },
      setCustom(field, value) {
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
      setSize(value) {
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
        this.applyBrief(data, sent, before, seen);
      },
      applyBrief(data, sent, before, seen) {
        const b = this.brief, got = plainObject(data.answers);
        const untouched = k => edits[k] === seen[k] && same(this.answers[k], before[k]);  // not set during the request
        for (const k of Object.keys(got)) {
          if (same(got[k], sent[k])) continue;  // the person's own answer, sent and kept
          if (!untouched(k)) continue;  // set by the person while the words were read
          if (same(got[k], this.answers[k])) continue;  // the same as already there: no change, no chip
          this.answers[k] = got[k];
          b.picked[k] = got[k];
          b.owned[k] = got[k];
          this.builder.fresh = this.builder.fresh.filter(x => x !== k);
        }
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
      rowText(s) {
        const v = this.answers[s.id];
        if (s.kind === 'colour' && v && typeof v === 'object') return v.name + ' \u00b7 ' + String(v.hex).toUpperCase();
        return capital(this.optionLabel(s, v));
      },
      missingSections() { return this.sections.filter(s => s.visible && this.missing.includes(s.id)); },
      editSection(id) {  // scroll to a section and put the keyboard on its choice
        const h = document.getElementById('sec-' + id);
        const sec = h && h.closest('section');
        if (!sec) return;
        sec.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
        let target = null;
        if (id === 'colour' && this.builder.custom.open) {
          target = sec.querySelector(this.builder.custom.name ? '#custom-colour .hex' : '#custom-colour input');
        }
        target = target || sec.querySelector('[aria-pressed="true"]:not(:disabled)')
          || sec.querySelector('input:not(:disabled), .row button:not(:disabled), .seg button:not(:disabled)');
        if (target) target.focus({ preventScroll: true });
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
    };
  }

  document.addEventListener('alpine:init', () => {
    Alpine.store('studio', createStudio());
    Alpine.store('studio').init();
  });
})();
