(function () {
    const grid = document.getElementById('ugc-card-grid');
    if (!grid) return;

    const discoveredTab = document.querySelector('a[href*="tab=discovered"].border-violet-500');
    if (!discoveredTab) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    if (!cards.length) return;

    function creatorHandleFor(card) {
        const contributorLine = Array.from(card.querySelectorAll('p')).find((node) => (node.textContent || '').trim().startsWith('@'));
        if (contributorLine) {
            const match = (contributorLine.textContent || '').match(/@([A-Za-z0-9._]+)/);
            if (match) return match[1];
        }
        const match = (card.textContent || '').match(/@([A-Za-z0-9._]+)/);
        return match ? match[1] : '';
    }

    function titleFor(card) {
        const heading = card.querySelector('h2');
        return heading ? (heading.textContent || '').trim() : 'your post';
    }

    function permissionMessage(card) {
        const handle = creatorHandleFor(card);
        const title = titleFor(card);
        const greeting = handle ? `Hi @${handle}!` : 'Hi!';
        const credit = handle ? ` We’ll credit you as @${handle} and link back to your original post.` : ' We’ll credit you and link back to your original post.';
        return `${greeting} We came across your ${title} post and would love to feature it on The TN Game’s social media and website.${credit} If you’re okay with us sharing it, please reply YES to this message. Thank you!`;
    }

    async function copyText(text) {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return;
        }
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand('copy');
        textarea.remove();
        if (!copied) throw new Error('Copy failed');
    }

    function extractSubmissionId(card) {
        const form = card.querySelector('form[action*="/permission/"]');
        if (!form) return '';
        const match = form.getAttribute('action').match(/\/([0-9a-fA-F-]{36})\/permission\/?$/);
        return match ? match[1] : '';
    }

    function permissionStatusFor(card) {
        const form = card.querySelector('form[action*="/permission/"]');
        const panel = form ? form.closest('div.mt-3') : null;
        const text = (panel ? panel.textContent : card.textContent || '').toLocaleLowerCase();
        if (text.includes('permission requested')) return 'requested';
        if (text.includes('permission declined')) return 'declined';
        if (text.includes('permission granted')) return 'granted';
        return 'not_contacted';
    }

    cards.forEach((card) => {
        card.dataset.permissionStatus = permissionStatusFor(card);
        const permissionForm = card.querySelector('form[action*="/permission/"]');
        if (!permissionForm) return;
        const permissionPanel = permissionForm.closest('div.mt-3');
        if (!permissionPanel || permissionPanel.querySelector('.ugc-copy-permission-request')) return;

        const statusRow = permissionPanel.querySelector('.flex.items-start.justify-between');
        if (!statusRow) return;

        const copyButton = document.createElement('button');
        copyButton.type = 'button';
        copyButton.className = 'ugc-copy-permission-request ml-auto mr-2 inline-flex items-center gap-1 px-2 py-1 rounded-md border border-stone-200 bg-white text-[10px] font-semibold text-stone-600 hover:bg-stone-50 hover:text-stone-900';
        copyButton.innerHTML = '<svg width="11" height="11" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg><span>Copy request</span>';
        copyButton.title = permissionMessage(card);

        const statusDot = statusRow.lastElementChild;
        if (statusDot) statusRow.insertBefore(copyButton, statusDot);
        else statusRow.appendChild(copyButton);

        copyButton.addEventListener('click', async function () {
            const label = copyButton.querySelector('span');
            try {
                await copyText(permissionMessage(card));
                label.textContent = 'Copied';
                copyButton.classList.add('text-emerald-700', 'border-emerald-200', 'bg-emerald-50');
                window.setTimeout(() => {
                    label.textContent = 'Copy request';
                    copyButton.classList.remove('text-emerald-700', 'border-emerald-200', 'bg-emerald-50');
                }, 1800);
            } catch (error) {
                label.textContent = 'Copy failed';
                window.setTimeout(() => { label.textContent = 'Copy request'; }, 1800);
            }
        });
    });

    const csrfInput = cards
        .map((card) => card.querySelector('form[action*="/permission/"] input[name="csrfmiddlewaretoken"]'))
        .find(Boolean);
    if (!csrfInput) return;

    const toolbar = document.createElement('form');
    toolbar.method = 'post';
    toolbar.action = window.location.pathname.replace(/\/?$/, '/discovered/bulk/permission/');
    toolbar.id = 'ugc-bulk-permission-form';
    toolbar.className = 'mb-4 rounded-xl border border-violet-200 bg-violet-50/60 px-3 py-2.5 flex flex-col sm:flex-row sm:items-center gap-2 sm:justify-between';
    toolbar.innerHTML = `
        <input type="hidden" name="csrfmiddlewaretoken" value="${csrfInput.value}">
        <input type="hidden" name="channel" value="bulk">
        <div class="flex items-center gap-3 min-w-0">
            <label class="inline-flex items-center gap-2 text-xs font-semibold text-violet-900 cursor-pointer whitespace-nowrap">
                <input id="ugc-select-visible" type="checkbox" class="w-4 h-4 rounded border-violet-300 accent-violet-600">
                Select all visible
            </label>
            <span id="ugc-selected-count" class="text-[11px] text-violet-600">0 selected</span>
        </div>
        <div class="flex flex-wrap items-center gap-1.5">
            <button type="submit" name="permission_status" value="requested" disabled class="ugc-bulk-action px-3 py-1.5 text-[11px] font-semibold rounded-lg border border-amber-200 bg-white text-amber-700 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-amber-50">Mark requested</button>
            <button type="submit" name="permission_status" value="granted" disabled class="ugc-bulk-action px-3 py-1.5 text-[11px] font-semibold rounded-lg bg-emerald-600 text-white disabled:opacity-40 disabled:cursor-not-allowed hover:bg-emerald-700">Permission granted</button>
            <button type="submit" name="permission_status" value="declined" disabled class="ugc-bulk-action px-3 py-1.5 text-[11px] font-semibold rounded-lg border border-red-200 bg-white text-red-700 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-red-50">Declined</button>
        </div>
        <div id="ugc-bulk-hidden-inputs"></div>
    `;
    grid.parentNode.insertBefore(toolbar, grid);

    const selectVisible = document.getElementById('ugc-select-visible');
    const selectedCount = document.getElementById('ugc-selected-count');
    const hiddenInputs = document.getElementById('ugc-bulk-hidden-inputs');
    const actionButtons = Array.from(toolbar.querySelectorAll('.ugc-bulk-action'));

    cards.forEach((card) => {
        const id = extractSubmissionId(card);
        if (!id) return;
        card.dataset.submissionId = id;
        const label = document.createElement('label');
        label.className = 'ugc-card-select absolute top-2 left-2 z-20 inline-flex items-center justify-center w-8 h-8 rounded-lg bg-white/95 border border-stone-200 shadow-sm cursor-pointer';
        label.title = 'Select discovered item';
        label.innerHTML = `<input type="checkbox" class="ugc-select-item w-4 h-4 accent-violet-600" value="${id}" aria-label="Select this discovered item">`;
        card.classList.add('relative');
        card.appendChild(label);
    });

    const itemCheckboxes = Array.from(document.querySelectorAll('.ugc-select-item'));

    const sortSelect = document.getElementById('ugc-sort');
    const sourceSelect = document.getElementById('ugc-source');
    const resultCount = document.getElementById('ugc-result-count');
    let permissionSelect = document.getElementById('ugc-permission-filter');

    if (!permissionSelect && sortSelect && sortSelect.parentNode) {
        permissionSelect = document.createElement('select');
        permissionSelect.id = 'ugc-permission-filter';
        permissionSelect.className = 'h-9 text-xs border border-stone-200 rounded-lg bg-white px-3 text-stone-700 outline-none focus:border-violet-400';
        permissionSelect.setAttribute('aria-label', 'Creator permission status');
        permissionSelect.innerHTML = `
            <option value="">All permissions</option>
            <option value="not_contacted">Not contacted</option>
            <option value="requested">Permission requested</option>
            <option value="declined">Declined</option>
        `;
        sortSelect.parentNode.insertBefore(permissionSelect, sortSelect);
    }

    function cardIsVisible(card) {
        return !card.classList.contains('hidden') && card.style.display !== 'none';
    }

    function visibleCheckboxes() {
        return itemCheckboxes.filter((checkbox) => {
            const card = checkbox.closest('.ugc-card');
            return card && cardIsVisible(card);
        });
    }

    function updateResultCount() {
        if (!resultCount) return;
        const visible = cards.filter(cardIsVisible).length;
        const permissionFiltering = Boolean(permissionSelect && permissionSelect.value);
        const sourceFiltering = Boolean(sourceSelect && sourceSelect.value);
        const search = document.getElementById('ugc-search');
        const searchFiltering = Boolean(search && (search.value || '').trim());
        if (permissionFiltering || sourceFiltering || searchFiltering) {
            resultCount.textContent = `${visible} of ${cards.length}`;
        } else {
            resultCount.textContent = `${cards.length} item${cards.length === 1 ? '' : 's'}`;
        }
    }

    function applyPermissionFilter() {
        const status = permissionSelect ? permissionSelect.value : '';
        cards.forEach((card) => {
            const matches = !status || card.dataset.permissionStatus === status;
            card.style.display = matches ? '' : 'none';
        });
        updateResultCount();
        sync();
    }

    function sync() {
        const selected = itemCheckboxes.filter((checkbox) => checkbox.checked);
        hiddenInputs.innerHTML = '';
        selected.forEach((checkbox) => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'submission_ids';
            input.value = checkbox.value;
            hiddenInputs.appendChild(input);
        });

        selectedCount.textContent = `${selected.length} selected`;
        actionButtons.forEach((button) => { button.disabled = selected.length === 0; });

        const visible = visibleCheckboxes();
        const visibleSelected = visible.filter((checkbox) => checkbox.checked);
        selectVisible.checked = visible.length > 0 && visibleSelected.length === visible.length;
        selectVisible.indeterminate = visibleSelected.length > 0 && visibleSelected.length < visible.length;
    }

    itemCheckboxes.forEach((checkbox) => checkbox.addEventListener('change', sync));
    selectVisible.addEventListener('change', function () {
        visibleCheckboxes().forEach((checkbox) => { checkbox.checked = selectVisible.checked; });
        sync();
    });

    function formatMetric(value) {
        const number = Number(value || 0);
        if (number >= 1000000) return `${(number / 1000000).toFixed(number >= 10000000 ? 0 : 1)}M`;
        if (number >= 1000) return `${(number / 1000).toFixed(number >= 10000 ? 0 : 1)}K`;
        return String(number);
    }

    function sortByEngagementIfNeeded() {
        if (!sortSelect || sortSelect.value !== 'engaged') return;
        cards.slice().sort((a, b) => {
            const scoreDiff = Number(b.dataset.engagement || 0) - Number(a.dataset.engagement || 0);
            if (scoreDiff) return scoreDiff;
            return Number(b.dataset.submitted || 0) - Number(a.dataset.submitted || 0);
        }).forEach((card) => grid.appendChild(card));
    }

    function addDiscoveryIntelligence(item) {
        const card = cards.find((candidate) => candidate.dataset.submissionId === item.id);
        if (!card) return;
        card.dataset.engagement = String(item.engagement_score || 0);

        const title = card.querySelector('h2');
        const contributor = title ? title.nextElementSibling : null;
        if (contributor && !card.querySelector('.ugc-discovery-intelligence')) {
            const row = document.createElement('div');
            row.className = 'ugc-discovery-intelligence mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-stone-500';
            const metrics = [];
            if (item.like_count) metrics.push(`<span class="inline-flex items-center gap-1 rounded-md bg-rose-50 px-1.5 py-0.5 text-rose-700">♥ ${formatMetric(item.like_count)}</span>`);
            if (item.comment_count) metrics.push(`<span class="inline-flex items-center gap-1 rounded-md bg-sky-50 px-1.5 py-0.5 text-sky-700">💬 ${formatMetric(item.comment_count)}</span>`);
            if (item.view_count) metrics.push(`<span class="inline-flex items-center gap-1 rounded-md bg-stone-100 px-1.5 py-0.5 text-stone-600">◉ ${formatMetric(item.view_count)}</span>`);
            if (item.discovery_query) metrics.push(`<span class="max-w-[170px] truncate rounded-md bg-violet-50 px-1.5 py-0.5 text-violet-700" title="Found via ${item.discovery_query.replace(/"/g, '&quot;')}">Found via ${item.discovery_query}</span>`);
            if (metrics.length) {
                row.innerHTML = metrics.join('');
                contributor.insertAdjacentElement('afterend', row);
                card.dataset.searchText = `${card.dataset.searchText || (card.textContent || '').toLocaleLowerCase()} ${(item.discovery_query || '').toLocaleLowerCase()}`;
            }
        }
    }

    if (sortSelect && !sortSelect.querySelector('option[value="engaged"]')) {
        const option = document.createElement('option');
        option.value = 'engaged';
        option.textContent = 'Most engaged';
        sortSelect.appendChild(option);
    }

    const intelligenceUrl = window.location.pathname.replace(/\/?$/, '/discovered/intelligence/');
    fetch(intelligenceUrl, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error('Discovery intelligence unavailable')))
        .then((payload) => {
            (payload.items || []).forEach(addDiscoveryIntelligence);
            sortByEngagementIfNeeded();
            applyPermissionFilter();
        })
        .catch(() => { applyPermissionFilter(); });

    ['ugc-search-submit', 'ugc-search-clear'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', () => window.setTimeout(() => { sortByEngagementIfNeeded(); applyPermissionFilter(); }, 0));
    });
    const emptyClear = document.getElementById('ugc-empty-clear');
    if (emptyClear) emptyClear.addEventListener('click', () => {
        if (permissionSelect) permissionSelect.value = '';
        window.setTimeout(() => { sortByEngagementIfNeeded(); applyPermissionFilter(); }, 0);
    });
    ['ugc-source', 'ugc-sort'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => window.setTimeout(() => { sortByEngagementIfNeeded(); applyPermissionFilter(); }, 0));
    });
    if (permissionSelect) permissionSelect.addEventListener('change', applyPermissionFilter);

    const search = document.getElementById('ugc-search');
    if (search) search.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') window.setTimeout(() => { sortByEngagementIfNeeded(); applyPermissionFilter(); }, 0);
    });

    toolbar.addEventListener('submit', function (event) {
        if (!itemCheckboxes.some((checkbox) => checkbox.checked)) event.preventDefault();
    });

    applyPermissionFilter();
})();
