(() => {
  'use strict';

  const storageKey = `tn-facebook-groups:${window.TN_FACEBOOK_GROUP_POST_KEY || 'new'}`;
  const state = { groups: [], selected: new Set(), activeIndex: 0 };

  function load() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || '{}');
      state.groups = Array.isArray(saved.groups) ? saved.groups : [];
      state.selected = new Set(Array.isArray(saved.selected) ? saved.selected : []);
    } catch (_) {
      state.groups = [];
      state.selected = new Set();
    }
  }
  function persist() { localStorage.setItem(storageKey, JSON.stringify({ groups: state.groups, selected: [...state.selected] })); }
  function normalizeUrl(value) {
    try {
      const u = new URL(value);
      const host = u.hostname.toLowerCase();
      if (!['facebook.com', 'www.facebook.com', 'm.facebook.com'].includes(host) || !u.pathname.startsWith('/groups/')) return '';
      return `https://www.facebook.com${u.pathname.replace(/\/+$/, '')}/`;
    } catch (_) { return ''; }
  }
  function selectedGroups() { return state.groups.filter((g) => state.selected.has(g.url)); }
  function captionValue() { return document.querySelector('[name="caption"]')?.value || ''; }
  function mediaCount() { return document.querySelectorAll('.media-thumb img').length; }

  function buildPanel() {
    if (document.getElementById('tn-fb-groups-panel')) return;
    const form = document.getElementById('composer-form');
    if (!form) return;
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
        <div class="tn-fbg-note">Facebook discontinued third-party Groups publishing. Studio opens each selected group and prepares the caption so you can publish safely from Facebook.</div>
      </div>`;
    const center = form.querySelector('.panel-center') || form.firstElementChild || form;
    center.prepend(panel);
    const collapse = panel.querySelector('.tn-fbg-collapse');
    const body = panel.querySelector('.tn-fbg-body');
    collapse.addEventListener('click', () => { const open = body.hidden; body.hidden = !open; collapse.setAttribute('aria-expanded', String(open)); collapse.textContent = open ? 'Hide' : 'Add groups'; });
    panel.querySelector('.tn-fbg-add').addEventListener('click', addGroup);
    panel.querySelector('.tn-fbg-url').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addGroup(); } });
    panel.querySelector('.tn-fbg-search').addEventListener('input', renderList);
    panel.querySelector('.tn-fbg-select-all').addEventListener('click', () => { state.groups.forEach((g) => state.selected.add(g.url)); persist(); renderList(); });
    panel.querySelector('.tn-fbg-clear').addEventListener('click', () => { state.selected.clear(); persist(); renderList(); });
    panel.querySelector('.tn-fbg-start').addEventListener('click', startAssistant);
    renderList();
  }

  function addGroup() {
    const panel = document.getElementById('tn-fb-groups-panel');
    const nameEl = panel.querySelector('.tn-fbg-name');
    const urlEl = panel.querySelector('.tn-fbg-url');
    const errorEl = panel.querySelector('.tn-fbg-error');
    const url = normalizeUrl(urlEl.value.trim());
    if (!url) { errorEl.textContent = 'Enter a valid Facebook Group URL.'; return; }
    const name = nameEl.value.trim() || 'Facebook Group';
    const existing = state.groups.find((g) => g.url === url);
    if (existing) existing.name = name; else state.groups.push({ name, url });
    state.selected.add(url);
    nameEl.value = ''; urlEl.value = ''; errorEl.textContent = '';
    persist(); renderList();
  }

  function renderList() {
    const panel = document.getElementById('tn-fb-groups-panel'); if (!panel) return;
    const list = panel.querySelector('.tn-fbg-list');
    const query = panel.querySelector('.tn-fbg-search').value.trim().toLowerCase();
    list.innerHTML = '';
    const groups = state.groups.filter((g) => !query || g.name.toLowerCase().includes(query) || g.url.toLowerCase().includes(query));
    if (!groups.length) {
      const empty = document.createElement('div'); empty.className = 'tn-fbg-empty';
      empty.textContent = state.groups.length ? 'No groups match your search.' : 'Save a Facebook Group above to use it as a destination.';
      list.appendChild(empty);
    }
    groups.forEach((group) => {
      const row = document.createElement('label'); row.className = 'tn-fbg-row';
      const box = document.createElement('input'); box.type = 'checkbox'; box.checked = state.selected.has(group.url);
      box.addEventListener('change', () => { if (box.checked) state.selected.add(group.url); else state.selected.delete(group.url); persist(); renderList(); });
      const text = document.createElement('span'); text.className = 'tn-fbg-row-text';
      const strong = document.createElement('strong'); strong.textContent = group.name;
      const small = document.createElement('small'); small.textContent = group.url; text.append(strong, small);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'tn-fbg-remove'; remove.textContent = 'Remove';
      remove.addEventListener('click', (e) => { e.preventDefault(); state.groups = state.groups.filter((g) => g.url !== group.url); state.selected.delete(group.url); persist(); renderList(); });
      row.append(box, text, remove); list.appendChild(row);
    });
    const selected = selectedGroups().length;
    panel.querySelector('.tn-fbg-count').textContent = `${selected} group${selected === 1 ? '' : 's'} selected`;
    panel.querySelector('.tn-fbg-start').disabled = selected === 0;
  }

  function startAssistant() { const groups = selectedGroups(); if (!groups.length) return; state.activeIndex = 0; showAssistant(groups); }
  function showAssistant(groups) {
    document.getElementById('tn-fbg-assistant')?.remove();
    const overlay = document.createElement('div'); overlay.id = 'tn-fb-groups-assistant'; overlay.className = 'tn-fbg-overlay';
    overlay.innerHTML = `<div class="tn-fbg-modal" role="dialog" aria-modal="true" aria-label="Facebook Groups publishing assistant"><div class="tn-fbg-modal-head"><div><strong>Post to Facebook Groups</strong><div class="tn-fbg-step"></div></div><button type="button" class="tn-fbg-close" aria-label="Close">×</button></div><div class="tn-fbg-modal-body"><div class="tn-fbg-current"></div><div class="tn-fbg-caption-label">Caption</div><textarea class="tn-fbg-caption" readonly></textarea><div class="tn-fbg-media-note"></div><div class="tn-fbg-actions"><button type="button" class="tn-fbg-copy">Copy caption</button><button type="button" class="tn-fbg-open">Open group</button></div></div><div class="tn-fbg-modal-foot"><button type="button" class="tn-fbg-skip">Skip</button><button type="button" class="tn-fbg-posted">Mark posted & next</button></div></div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector('.tn-fbg-close').addEventListener('click', close);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    overlay.querySelector('.tn-fbg-copy').addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(captionValue()); } catch (_) { const t = overlay.querySelector('.tn-fbg-caption'); t.select(); document.execCommand('copy'); }
      const b = overlay.querySelector('.tn-fbg-copy'); b.textContent = 'Copied'; setTimeout(() => { if (b.isConnected) b.textContent = 'Copy caption'; }, 1200);
    });
    overlay.querySelector('.tn-fbg-open').addEventListener('click', () => window.open(groups[state.activeIndex].url, '_blank', 'noopener,noreferrer'));
    overlay.querySelector('.tn-fbg-skip').addEventListener('click', () => advance(groups, overlay));
    overlay.querySelector('.tn-fbg-posted').addEventListener('click', () => advance(groups, overlay));
    renderAssistantStep(groups, overlay);
  }
  function advance(groups, overlay) {
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
    const count = mediaCount(); overlay.querySelector('.tn-fbg-media-note').textContent = count ? `${count} media item${count === 1 ? '' : 's'} attached in Studio. Add the same media in Facebook after the group opens.` : 'No media detected on this post.';
  }

  load();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', buildPanel); else buildPanel();
})();
