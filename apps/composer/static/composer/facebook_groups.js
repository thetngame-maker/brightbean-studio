(() => {
  'use strict';

  // Keep runtime configuration on this same-origin script tag. Production's
  // CSP intentionally blocks inline JavaScript, so an injected `window.*`
  // assignment would leave the visible panel without its API URL.
  const loader = document.currentScript || document.getElementById('tn-facebook-groups-script');
  const legacyApi = window.TN_FACEBOOK_GROUPS_API || {};
  const api = {
    catalogUrl: loader?.dataset.catalogUrl || legacyApi.catalogUrl || '',
    postKey: loader?.dataset.postKey || legacyApi.postKey || 'new',
  };
  const newSelectionKey = `tn-facebook-groups:new:${location.pathname}`;
  const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const state = {
    groups: [],
    selected: new Set(),
    statuses: new Map(),
    activeIndex: 0,
    postId: api.postKey && api.postKey !== 'new' ? api.postKey : '',
    loading: true,
  };

  function csrfToken() {
    const hidden = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value;
    if (hidden) return hidden;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  async function request(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set('X-Requested-With', 'XMLHttpRequest');
    if ((options.method || 'GET').toUpperCase() !== 'GET') headers.set('X-CSRFToken', csrfToken());
    const response = await fetch(url, { credentials: 'same-origin', ...options, headers });
    let data = {};
    try { data = await response.json(); } catch (_) { /* no-op */ }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }

  function postUrl(postId) {
    return api.catalogUrl ? `${api.catalogUrl}post/${postId}/` : '';
  }

  function detectedPostId() {
    if (state.postId && uuidRe.test(state.postId)) return state.postId;
    const hidden = document.querySelector('[name="_autosave_post_id"]')?.value?.trim();
    if (hidden && uuidRe.test(hidden)) return hidden;
    const match = location.pathname.match(/\/compose\/([0-9a-f-]{36})\/?$/i);
    return match && uuidRe.test(match[1]) ? match[1] : '';
  }

  function loadLocalNewSelection() {
    try {
      const ids = JSON.parse(localStorage.getItem(newSelectionKey) || '[]');
      state.selected = new Set(Array.isArray(ids) ? ids : []);
    } catch (_) { state.selected = new Set(); }
  }

  function saveLocalNewSelection() {
    if (!state.postId) localStorage.setItem(newSelectionKey, JSON.stringify([...state.selected]));
  }

  async function loadState() {
    loadLocalNewSelection();
    if (!api.catalogUrl) { state.loading = false; renderList(); return; }
    try {
      const catalog = await request(api.catalogUrl);
      state.groups = Array.isArray(catalog.groups) ? catalog.groups : [];
      const postId = detectedPostId();
      if (postId) {
        state.postId = postId;
        const saved = await request(postUrl(postId));
        state.selected = new Set((saved.groups || []).map((group) => group.id));
        state.statuses = new Map((saved.groups || []).map((group) => [group.id, group.status || 'pending']));
        localStorage.removeItem(newSelectionKey);
      }
    } catch (error) {
      showError(error.message || 'Could not load Facebook Groups.');
    } finally {
      state.loading = false;
      renderList();
      maybeStartReadyAssistant();
    }
  }

  async function persistSelection() {
    const postId = detectedPostId();
    if (!postId) { saveLocalNewSelection(); return; }
    state.postId = postId;
    if (!api.catalogUrl) return;
    const body = new URLSearchParams();
    body.set('action', 'set');
    body.set('group_ids', [...state.selected].join(','));
    try {
      const data = await request(postUrl(postId), { method: 'POST', body });
      state.statuses = new Map((data.groups || []).map((group) => [group.id, group.status || 'pending']));
      localStorage.removeItem(newSelectionKey);
    } catch (error) {
      showError(error.message || 'Could not save group selections.');
    }
  }

  function selectedGroups() { return state.groups.filter((group) => state.selected.has(group.id)); }
  function pendingSelectedGroups() {
    return selectedGroups().filter((group) => (state.statuses.get(group.id) || 'pending') === 'pending');
  }
  function captionValue() { return document.querySelector('[name="caption"]')?.value || ''; }
  function mediaCount() { return document.querySelectorAll('.media-thumb img').length; }

  function showError(message) {
    const node = document.querySelector('#tn-fb-groups-panel .tn-fbg-error');
    if (node) node.textContent = message || '';
  }

  function buildPanel() {
    if (document.getElementById('tn-fb-groups-panel')) return;
    const form = document.getElementById('composer-form');
    if (!form) return;
    const scrollArea = form.querySelector('.panel-scroll');
    const accountRow = scrollArea?.querySelector('.acct-pill')?.parentElement
      || scrollArea?.querySelector('.flex.items-center.gap-2.flex-wrap');

    const panel = document.createElement('section');
    panel.id = 'tn-fb-groups-panel';
    panel.className = 'tn-fbg-panel';
    panel.innerHTML = `
      <div class="tn-fbg-head">
        <div><div class="tn-fbg-title"><span class="tn-fbg-icon">f</span> Facebook Groups</div><div class="tn-fbg-subtitle">Assisted publishing · select multiple groups</div></div>
        <button type="button" class="tn-fbg-collapse" aria-expanded="false">Add groups</button>
      </div>
      <div class="tn-fbg-body" hidden>
        <div class="tn-fbg-add-row"><input class="tn-fbg-name" type="text" maxlength="120" placeholder="Group name"><input class="tn-fbg-url" type="url" placeholder="https://facebook.com/groups/..."><button class="tn-fbg-add" type="button">Add</button></div>
        <div class="tn-fbg-error" role="status"></div>
        <div class="tn-fbg-toolbar"><input class="tn-fbg-search" type="search" placeholder="Search saved groups"><button type="button" class="tn-fbg-select-all">Select all</button><button type="button" class="tn-fbg-clear">Clear</button></div>
        <div class="tn-fbg-list"></div>
        <div class="tn-fbg-footer"><span class="tn-fbg-count">0 groups selected</span><button type="button" class="tn-fbg-start">Post to selected groups</button></div>
        <div class="tn-fbg-note">Facebook discontinued third-party Groups publishing. Studio opens each selected group and prepares the caption so you can publish safely from Facebook. Saved groups and per-post selections are stored in your workspace.</div>
      </div>`;

    if (accountRow) accountRow.insertAdjacentElement('afterend', panel);
    else (scrollArea || form).prepend(panel);

    const collapse = panel.querySelector('.tn-fbg-collapse');
    const body = panel.querySelector('.tn-fbg-body');

    if (accountRow) {
      const destinationPill = document.createElement('button');
      destinationPill.type = 'button';
      destinationPill.id = 'tn-fbg-destination-pill';
      destinationPill.className = 'acct-pill tn-fbg-destination-pill';
      destinationPill.innerHTML = '<span class="tn-fbg-icon">f</span><span class="text-sm font-medium text-stone-700">Facebook Groups</span><span class="tn-fbg-pill-count" hidden></span>';
      destinationPill.addEventListener('click', () => {
        body.hidden = false;
        collapse.setAttribute('aria-expanded', 'true');
        collapse.textContent = 'Hide';
        panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      });
      accountRow.appendChild(destinationPill);
    }

    collapse.addEventListener('click', () => {
      const open = body.hidden; body.hidden = !open;
      collapse.setAttribute('aria-expanded', String(open)); collapse.textContent = open ? 'Hide' : 'Add groups';
    });
    panel.querySelector('.tn-fbg-add').addEventListener('click', addGroup);
    panel.querySelector('.tn-fbg-url').addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); addGroup(); } });
    panel.querySelector('.tn-fbg-search').addEventListener('input', renderList);
    panel.querySelector('.tn-fbg-select-all').addEventListener('click', async () => {
      state.groups.forEach((group) => state.selected.add(group.id)); renderList(); await persistSelection();
    });
    panel.querySelector('.tn-fbg-clear').addEventListener('click', async () => {
      state.selected.clear(); state.statuses.clear(); renderList(); await persistSelection();
    });
    panel.querySelector('.tn-fbg-start').addEventListener('click', () => startAssistant());
    renderList();
    loadState();

    document.body.addEventListener('htmx:afterRequest', async () => {
      const postId = detectedPostId();
      if (postId && !state.postId) { state.postId = postId; await persistSelection(); }
    });
    window.setInterval(async () => {
      const postId = detectedPostId();
      if (postId && !state.postId) { state.postId = postId; await persistSelection(); }
    }, 1500);
  }

  async function addGroup() {
    const panel = document.getElementById('tn-fb-groups-panel');
    const nameEl = panel.querySelector('.tn-fbg-name');
    const urlEl = panel.querySelector('.tn-fbg-url');
    const name = nameEl.value.trim() || 'Facebook Group';
    const url = urlEl.value.trim();
    showError('');
    if (!api.catalogUrl) { showError('Facebook Groups API is unavailable.'); return; }
    const body = new URLSearchParams(); body.set('action', 'add'); body.set('name', name); body.set('url', url);
    try {
      const data = await request(api.catalogUrl, { method: 'POST', body });
      const group = data.group;
      const index = state.groups.findIndex((item) => item.id === group.id);
      if (index >= 0) state.groups[index] = group; else state.groups.push(group);
      state.groups.sort((a, b) => a.name.localeCompare(b.name));
      state.selected.add(group.id);
      nameEl.value = ''; urlEl.value = '';
      renderList(); await persistSelection();
    } catch (error) { showError(error.message || 'Could not save this Facebook Group.'); }
  }

  async function removeGroup(group) {
    if (!api.catalogUrl) return;
    const body = new URLSearchParams(); body.set('action', 'remove'); body.set('group_id', group.id);
    try {
      await request(api.catalogUrl, { method: 'POST', body });
      state.groups = state.groups.filter((item) => item.id !== group.id);
      state.selected.delete(group.id); state.statuses.delete(group.id); renderList();
    } catch (error) { showError(error.message || 'Could not remove this group.'); }
  }

  function renderList() {
    const panel = document.getElementById('tn-fb-groups-panel'); if (!panel) return;
    const list = panel.querySelector('.tn-fbg-list');
    const query = panel.querySelector('.tn-fbg-search').value.trim().toLowerCase();
    list.innerHTML = '';
    if (state.loading) {
      const loading = document.createElement('div'); loading.className = 'tn-fbg-empty'; loading.textContent = 'Loading Facebook Groups…'; list.appendChild(loading);
    } else {
      const groups = state.groups.filter((group) => !query || group.name.toLowerCase().includes(query) || group.url.toLowerCase().includes(query));
      if (!groups.length) {
        const empty = document.createElement('div'); empty.className = 'tn-fbg-empty';
        empty.textContent = state.groups.length ? 'No groups match your search.' : 'Save a Facebook Group above to use it as a destination.';
        list.appendChild(empty);
      }
      groups.forEach((group) => {
        const row = document.createElement('label'); row.className = 'tn-fbg-row';
        const box = document.createElement('input'); box.type = 'checkbox'; box.checked = state.selected.has(group.id);
        box.addEventListener('change', async () => {
          if (box.checked) state.selected.add(group.id); else { state.selected.delete(group.id); state.statuses.delete(group.id); }
          renderList(); await persistSelection();
        });
        const text = document.createElement('span'); text.className = 'tn-fbg-row-text';
        const strong = document.createElement('strong'); strong.textContent = group.name;
        const small = document.createElement('small'); small.textContent = group.url; text.append(strong, small);
        const status = state.statuses.get(group.id);
        if (status && status !== 'pending') {
          const badge = document.createElement('span'); badge.className = `tn-fbg-status tn-fbg-status-${status}`; badge.textContent = status === 'posted' ? 'Posted' : 'Skipped'; text.appendChild(badge);
        }
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'tn-fbg-remove'; remove.textContent = 'Remove';
        remove.addEventListener('click', (event) => { event.preventDefault(); event.stopPropagation(); removeGroup(group); });
        row.append(box, text, remove); list.appendChild(row);
      });
    }
    const selected = selectedGroups().length;
    panel.querySelector('.tn-fbg-count').textContent = `${selected} group${selected === 1 ? '' : 's'} selected`;
    panel.querySelector('.tn-fbg-start').disabled = selected === 0;
    const pill = document.getElementById('tn-fbg-destination-pill');
    if (pill) {
      pill.classList.toggle('selected', selected > 0);
      const count = pill.querySelector('.tn-fbg-pill-count');
      count.hidden = selected === 0;
      count.textContent = String(selected);
    }
  }

  function startAssistant({ pendingOnly = false } = {}) {
    const pending = pendingSelectedGroups();
    const groups = pendingOnly || pending.length ? pending : selectedGroups();
    if (!groups.length) return;
    state.activeIndex = 0; showAssistant(groups);
  }

  function maybeStartReadyAssistant() {
    if (new URLSearchParams(location.search).get('facebook_groups') !== 'ready') return;
    const groups = pendingSelectedGroups();
    if (!groups.length) return;
    startAssistant({ pendingOnly: true });
  }

  function showAssistant(groups) {
    document.getElementById('tn-fb-groups-assistant')?.remove();
    const overlay = document.createElement('div'); overlay.id = 'tn-fb-groups-assistant'; overlay.className = 'tn-fbg-overlay';
    overlay.innerHTML = `<div class="tn-fbg-modal" role="dialog" aria-modal="true" aria-label="Facebook Groups publishing assistant"><div class="tn-fbg-modal-head"><div><strong>Post to Facebook Groups</strong><div class="tn-fbg-step"></div></div><button type="button" class="tn-fbg-close" aria-label="Close">×</button></div><div class="tn-fbg-modal-body"><div class="tn-fbg-current"></div><div class="tn-fbg-caption-label">Caption</div><textarea class="tn-fbg-caption" readonly></textarea><div class="tn-fbg-media-note"></div><div class="tn-fbg-actions"><button type="button" class="tn-fbg-copy">Copy caption</button><button type="button" class="tn-fbg-open">Open group</button></div></div><div class="tn-fbg-modal-foot"><button type="button" class="tn-fbg-skip">Skip</button><button type="button" class="tn-fbg-posted">Mark posted & next</button></div></div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector('.tn-fbg-close').addEventListener('click', close);
    overlay.addEventListener('click', (event) => { if (event.target === overlay) close(); });
    overlay.querySelector('.tn-fbg-copy').addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(captionValue()); }
      catch (_) { const textarea = overlay.querySelector('.tn-fbg-caption'); textarea.select(); document.execCommand('copy'); }
      const button = overlay.querySelector('.tn-fbg-copy'); button.textContent = 'Copied';
      setTimeout(() => { if (button.isConnected) button.textContent = 'Copy caption'; }, 1200);
    });
    overlay.querySelector('.tn-fbg-open').addEventListener('click', () => window.open(groups[state.activeIndex].url, '_blank', 'noopener,noreferrer'));
    overlay.querySelector('.tn-fbg-skip').addEventListener('click', () => advance(groups, overlay, 'skipped'));
    overlay.querySelector('.tn-fbg-posted').addEventListener('click', () => advance(groups, overlay, 'posted'));
    renderAssistantStep(groups, overlay);
  }

  async function markStatus(group, status) {
    state.statuses.set(group.id, status); renderList();
    const postId = detectedPostId();
    if (!postId || !api.catalogUrl) return;
    const body = new URLSearchParams(); body.set('action', 'status'); body.set('group_id', group.id); body.set('status', status);
    try { await request(postUrl(postId), { method: 'POST', body }); }
    catch (error) { showError(error.message || 'Could not update group posting status.'); }
  }

  async function advance(groups, overlay, status) {
    const group = groups[state.activeIndex]; await markStatus(group, status);
    state.activeIndex += 1;
    if (state.activeIndex >= groups.length) {
      overlay.querySelector('.tn-fbg-modal').innerHTML = `<div class="tn-fbg-done"><strong>Group handoff complete</strong><p>You worked through all ${groups.length} selected group${groups.length === 1 ? '' : 's'}.</p><button type="button">Done</button></div>`;
      overlay.querySelector('button').addEventListener('click', () => overlay.remove()); return;
    }
    renderAssistantStep(groups, overlay);
  }

  function renderAssistantStep(groups, overlay) {
    const group = groups[state.activeIndex];
    overlay.querySelector('.tn-fbg-step').textContent = `Group ${state.activeIndex + 1} of ${groups.length}`;
    const current = overlay.querySelector('.tn-fbg-current'); current.innerHTML = '';
    const strong = document.createElement('strong'); strong.textContent = group.name;
    const small = document.createElement('small'); small.textContent = group.url; current.append(strong, small);
    overlay.querySelector('.tn-fbg-caption').value = captionValue();
    const count = mediaCount();
    overlay.querySelector('.tn-fbg-media-note').textContent = count ? `${count} media item${count === 1 ? '' : 's'} attached in Studio. Add the same media in Facebook after the group opens.` : 'No media detected on this post.';
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', buildPanel); else buildPanel();
})();
