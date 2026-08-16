(function () {
    const grid = document.getElementById('ugc-card-grid');
    if (!grid) return;

    const discoveredTab = document.querySelector('a[href*="tab=discovered"].border-violet-500');
    if (!discoveredTab) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    if (!cards.length) return;

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

    function extractSubmissionId(card) {
        const form = card.querySelector('form[action*="/permission/"]');
        if (!form) return '';
        const match = form.getAttribute('action').match(/\/([0-9a-fA-F-]{36})\/permission\/?$/);
        return match ? match[1] : '';
    }

    cards.forEach((card) => {
        const id = extractSubmissionId(card);
        if (!id) return;
        const label = document.createElement('label');
        label.className = 'ugc-card-select absolute top-2 left-2 z-20 inline-flex items-center justify-center w-8 h-8 rounded-lg bg-white/95 border border-stone-200 shadow-sm cursor-pointer';
        label.title = 'Select discovered item';
        label.innerHTML = `<input type="checkbox" class="ugc-select-item w-4 h-4 accent-violet-600" value="${id}" aria-label="Select this discovered item">`;
        card.classList.add('relative');
        card.appendChild(label);
    });

    const itemCheckboxes = Array.from(document.querySelectorAll('.ugc-select-item'));

    function visibleCheckboxes() {
        return itemCheckboxes.filter((checkbox) => {
            const card = checkbox.closest('.ugc-card');
            return card && !card.classList.contains('hidden');
        });
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

    ['ugc-search-submit', 'ugc-search-clear', 'ugc-empty-clear'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', () => window.setTimeout(sync, 0));
    });
    ['ugc-source', 'ugc-sort'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => window.setTimeout(sync, 0));
    });
    const search = document.getElementById('ugc-search');
    if (search) search.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') window.setTimeout(sync, 0);
    });

    toolbar.addEventListener('submit', function (event) {
        if (!itemCheckboxes.some((checkbox) => checkbox.checked)) event.preventDefault();
    });

    sync();
})();
