// CSC Studio page: one Alpine store, organised by concern (storage, api, builder rules, then the
// store: budget, tabs, builder questions, answers, preview). Later tasks add the summary and jobs.
// Only UI conveniences go in localStorage (tour seen, draft of the answers), always in try/catch.
(function () {
  const BUDGET_REFRESH_MS = 60000;
  const QUESTIONS_DEBOUNCE_MS = 150;
  const PREVIEW_DEBOUNCE_MS = 300;
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
  };

  function plainObject(v) { return v && typeof v === 'object' && !Array.isArray(v) ? v : {}; }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  // timers and request numbers: a reply is used only if no newer request was made since
  let questionsTimer = null, questionsSeq = 0, previewTimer = null, previewSeq = 0;

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
        seen: null, fresh: [], tips: {},
        custom: { open: false, name: '', hex: '' },
      },
      // preview: prompt and cost for complete answers from /api/preview (shown by task 6)
      preview: null,
      // job / jobs: the job being watched and the job list (task 7)
      job: null,
      jobs: [],
      // budget: figures from /api/health
      budget: { gemini_usd: 0, cap_usd: 9, cf_neurons: 0, cf_cap: 9500, loaded: false, error: '' },
      // ui: current tab, guide panel, tour (task 8)
      ui: { tabs: TABS, tab: 'create', note: '', guideOpen: false, tourSeen: storage.get('tourSeen', false), tour: null },

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
      askQuestions() {  // each change re-posts the answers, debounced
        clearTimeout(questionsTimer);
        clearTimeout(previewTimer);
        previewSeq++;  // a preview in flight is for older answers
        questionsTimer = setTimeout(() => { questionsTimer = null; this.loadQuestions(); }, QUESTIONS_DEBOUNCE_MS);
      },
      async loadQuestions() {
        const seq = ++questionsSeq;
        const sent = JSON.parse(JSON.stringify(this.answers));
        let res;
        try {
          res = await api('/api/questions', post({ answers: sent }));
        } catch (e) {
          if (seq === questionsSeq) this.builder.error = NOTES.options;
          return;
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
      newBadge(s) { return NEW_REASONS[s.id] ? 'NEW \u00b7 ' + NEW_REASONS[s.id] : 'NEW'; },
      hoodie(id) { return { href: HOODIES[id] || '#h-classic' }; },  // x-bind object for an SVG <use>
      tipLabel(s) { return 'More about \u201c' + s.title + '\u201d'; },
      toggleTip(s) { this.builder.tips[s.id] = !this.builder.tips[s.id]; },
      tipOpen(s) { return !!this.builder.tips[s.id]; },

      // --- builder: answers -----------------------------------------------------------------
      setAnswer(id, value) {
        if (value === null || value === undefined) delete this.answers[id];
        else this.answers[id] = value;
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
        if (s.kind === 'colour') this.builder.custom.open = false;
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
      },
      async loadPreview(seq) {
        if (seq !== previewSeq) return;
        try {
          const p = await api('/api/preview', post({ answers: this.liveAnswers() }));
          if (seq !== previewSeq) return;
          this.preview = p;
          this.builder.previewError = '';
        } catch (e) {
          if (seq !== previewSeq) return;
          this.preview = null;
          this.builder.previewError = NOTES.preview;
        }
      },
    };
  }

  document.addEventListener('alpine:init', () => {
    Alpine.store('studio', createStudio());
    Alpine.store('studio').init();
  });
})();
