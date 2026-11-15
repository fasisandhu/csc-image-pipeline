// CSC Studio page: one Alpine store. Sections below are filled in by later tasks.
// Only UI conveniences go in localStorage (tour seen, draft of the answers), always in try/catch.
(function () {
  const BUDGET_REFRESH_MS = 60000;
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

  // --- store ----------------------------------------------------------------------------------
  function createStudio() {
    return {
      // answers: what the person picked, by question id (task 5)
      answers: storage.get('answers', {}),
      // sections: the question list from /api/questions (task 5)
      sections: [],
      // preview: prompt, cost and layout sketches for the current answers (task 6)
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
    };
  }

  document.addEventListener('alpine:init', () => {
    Alpine.store('studio', createStudio());
    Alpine.store('studio').init();
  });
})();
