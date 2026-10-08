/* Pure draft transitions, shared by the H5 UI and its regression checks. */
(function (root) {
  'use strict';
  const editable = ['who', 'what', 'whenIso', 'whenRaw', 'constraint'];
  const api = {
    create(data) {
      const safe = { ...data };
      if (safe.reviewStatus !== 'confirmed' && ['B', 'C'].includes(safe.tier)) safe.who = '';
      return { data: safe, model: { ...safe }, status: 'editing', result: null, error: null,
        submitted: safe.reviewStatus === 'confirmed', dirty: {}, reviewStatus: safe.reviewStatus || 'unreviewed' };
    },
    edit(state, field, value) {
      if (!state || state.status !== 'editing' || state.submitted || !editable.includes(field)) return false;
      state.data[field] = value;
      state.dirty[field] = true;
      return true;
    },
    merge(state, incoming) {
      if (!state || state.submitted || state.reviewStatus === 'confirmed' || state.status !== 'editing' || incoming.draftId !== state.data.draftId ||
          Number(incoming.modelRevision || 0) <= Number(state.model.modelRevision || 0)) return false;
      const retained = {};
      editable.forEach(k => { if (state.dirty[k]) retained[k] = state.data[k]; });
      state.model = { ...incoming };
      state.data = { ...incoming, ...retained };
      if (!state.dirty.who && ['B', 'C'].includes(incoming.tier)) state.data.who = '';
      return true;
    },
    confirm(state) {
      state.status = 'sending';
      state.reviewStatus = 'confirmed';
      state.data.reviewStatus = 'confirmed';
      state.data.ownerSelectionSource = state.dirty.who ? 'user' : 'model';
      // Model confidence and tier remain the model's measurements after a human selection.
      return { ...state.data };
    },
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.OopsDraftState = api;
})(typeof window !== 'undefined' ? window : globalThis);
