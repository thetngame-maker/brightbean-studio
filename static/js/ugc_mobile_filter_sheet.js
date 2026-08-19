(function () {
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    if (!document.body.classList.contains('ugc-mobile-community')) return;

    const panel = document.getElementById('ugc-mobile-filter-panel');
    const oldToggle = document.getElementById('ugc-mobile-filter-toggle');
    if (!panel || !oldToggle || document.getElementById('ugc-ios-filter-sheet')) return;

    const style = document.createElement('style');
    style.id = 'ugc-ios-filter-sheet-style';
    style.textContent = `
        @media (max-width: 767px) {
            body.ugc-mobile-community #ugc-mobile-filter-panel { display:none !important; }
            body.ugc-mobile-community .ugc-ios-filter-trigger {
                min-height:44px; border:0; border-radius:12px; background:#f2f2f7; color:#1c1c1e;
                display:inline-flex; align-items:center; justify-content:center; gap:6px; padding:0 14px;
                font-size:14px; font-weight:600; -webkit-tap-highlight-color:transparent;
            }
            body.ugc-mobile-community .ugc-ios-filter-trigger .ugc-ios-filter-count {
                min-width:20px; height:20px; padding:0 6px; border-radius:999px; background:#7c3aed; color:white;
                display:none; align-items:center; justify-content:center; font-size:11px; line-height:20px;
            }
            body.ugc-mobile-community .ugc-ios-filter-trigger.has-filters .ugc-ios-filter-count { display:inline-flex; }
            .ugc-ios-filter-backdrop {
                position:fixed; inset:0; z-index:420; background:rgba(0,0,0,.28); opacity:0; pointer-events:none;
                transition:opacity 180ms ease; backdrop-filter:blur(2px); -webkit-backdrop-filter:blur(2px);
            }
            .ugc-ios-filter-backdrop.open { opacity:1; pointer-events:auto; }
            .ugc-ios-filter-sheet {
                position:fixed; left:0; right:0; bottom:0; z-index:421; max-height:min(82vh,760px); background:#f2f2f7;
                border-radius:24px 24px 0 0; transform:translateY(105%); transition:transform 220ms cubic-bezier(.22,.8,.2,1);
                box-shadow:0 -12px 45px rgba(0,0,0,.16); padding-bottom:max(14px,env(safe-area-inset-bottom)); overflow:hidden;
            }
            .ugc-ios-filter-sheet.open { transform:translateY(0); }
            .ugc-ios-filter-handle-wrap { height:22px; display:flex; align-items:center; justify-content:center; }
            .ugc-ios-filter-handle { width:38px; height:5px; border-radius:999px; background:#c7c7cc; }
            .ugc-ios-filter-nav { display:flex; align-items:center; justify-content:space-between; padding:6px 18px 12px; }
            .ugc-ios-filter-nav h3 { margin:0; font-size:18px; font-weight:700; color:#1c1c1e; }
            .ugc-ios-filter-nav button { border:0; background:transparent; color:#7c3aed; font-size:15px; font-weight:600; padding:6px 0; }
            .ugc-ios-filter-scroll { overflow:auto; max-height:calc(min(82vh,760px) - 80px); padding:0 14px 18px; -webkit-overflow-scrolling:touch; }
            .ugc-ios-filter-section-label { margin:16px 12px 7px; color:#8e8e93; text-transform:uppercase; font-size:11px; letter-spacing:.04em; font-weight:600; }
            .ugc-ios-filter-group { background:#fff; border-radius:13px; overflow:hidden; }
            .ugc-ios-filter-row {
                width:100%; min-height:48px; border:0; border-bottom:.5px solid #d1d1d6; background:#fff; padding:0 14px;
                display:flex; align-items:center; justify-content:space-between; text-align:left; color:#1c1c1e; font-size:15px;
            }
            .ugc-ios-filter-row:last-child { border-bottom:0; }
            .ugc-ios-filter-row .check { width:22px; text-align:right; color:#7c3aed; font-size:17px; opacity:0; font-weight:700; }
            .ugc-ios-filter-row.selected .check { opacity:1; }
            .ugc-ios-filter-row:active { background:#ececf0; }
            .ugc-ios-filter-sheet .ugc-ios-reset-row { color:#d70015; justify-content:center; font-weight:600; }
            .ugc-ios-filter-sheet .ugc-ios-count-note { text-align:center; color:#8e8e93; font-size:12px; padding:12px 4px 0; }
        }
    `;
    document.head.appendChild(style);

    const trigger = oldToggle.cloneNode(false);
    trigger.id = 'ugc-mobile-filter-toggle';
    trigger.type = 'button';
    trigger.className = 'ugc-ios-filter-trigger';
    trigger.innerHTML = '<span>Filters</span><span class="ugc-ios-filter-count">0</span>';
    oldToggle.replaceWith(trigger);

    const backdrop = document.createElement('div');
    backdrop.className = 'ugc-ios-filter-backdrop';
    backdrop.id = 'ugc-ios-filter-backdrop';

    const sheet = document.createElement('div');
    sheet.id = 'ugc-ios-filter-sheet';
    sheet.className = 'ugc-ios-filter-sheet';
    sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-modal', 'true');
    sheet.setAttribute('aria-label', 'Filters and sort');
    sheet.innerHTML = `
        <div class="ugc-ios-filter-handle-wrap"><div class="ugc-ios-filter-handle"></div></div>
        <div class="ugc-ios-filter-nav">
            <button type="button" data-action="reset">Reset</button>
            <h3>Filters</h3>
            <button type="button" data-action="done">Done</button>
        </div>
        <div class="ugc-ios-filter-scroll"></div>
    `;
    document.body.appendChild(backdrop);
    document.body.appendChild(sheet);

    const scroll = sheet.querySelector('.ugc-ios-filter-scroll');
    const countBadge = trigger.querySelector('.ugc-ios-filter-count');

    const controls = [
        { id: 'ugc-media-filter', label: 'Media' },
        { id: 'ugc-discovery-method-filter', label: 'Discovery method' },
        { id: 'ugc-relevance-filter', label: 'Relevance', defaultValue: 'relevant' },
        { id: 'ugc-permission-filter', label: 'Permission' },
        { id: 'ugc-source', label: 'Source' },
        { id: 'ugc-sort', label: 'Sort', defaultValue: 'newest' }
    ];

    function dispatch(control) {
        control.dispatchEvent(new Event('change', { bubbles:true }));
        document.dispatchEvent(new CustomEvent('ugc:filters-changed'));
    }

    function selectedOption(control) {
        return control && control.options ? control.options[control.selectedIndex] : null;
    }

    function meaningful(control, config) {
        if (!control) return false;
        const value = String(control.value || '');
        const normal = config.defaultValue !== undefined ? String(config.defaultValue) : '';
        return value !== normal;
    }

    function activeCount() {
        return controls.reduce((sum, config) => {
            const control = document.getElementById(config.id);
            return sum + (meaningful(control, config) ? 1 : 0);
        }, 0);
    }

    function syncTrigger() {
        const count = activeCount();
        countBadge.textContent = String(count);
        trigger.classList.toggle('has-filters', count > 0);
        trigger.setAttribute('aria-label', count ? `Filters, ${count} active` : 'Filters');
    }

    function buildSection(config) {
        const control = document.getElementById(config.id);
        if (!control) return null;
        const fragment = document.createDocumentFragment();
        const label = document.createElement('div');
        label.className = 'ugc-ios-filter-section-label';
        label.textContent = config.label;
        fragment.appendChild(label);

        const group = document.createElement('div');
        group.className = 'ugc-ios-filter-group';
        Array.from(control.options || []).forEach((option) => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'ugc-ios-filter-row';
            row.dataset.control = config.id;
            row.dataset.value = option.value;
            row.innerHTML = `<span></span><span class="check">✓</span>`;
            row.firstElementChild.textContent = option.textContent || option.label || option.value;
            row.classList.toggle('selected', String(control.value) === String(option.value));
            row.addEventListener('click', () => {
                control.value = option.value;
                dispatch(control);
                group.querySelectorAll('.ugc-ios-filter-row').forEach((candidate) => candidate.classList.toggle('selected', candidate === row));
                syncTrigger();
                syncResultNote();
            });
            group.appendChild(row);
        });
        fragment.appendChild(group);
        return fragment;
    }

    function rebuild() {
        scroll.innerHTML = '';
        controls.forEach((config) => {
            const section = buildSection(config);
            if (section) scroll.appendChild(section);
        });

        const resetLabel = document.createElement('div');
        resetLabel.className = 'ugc-ios-filter-section-label';
        resetLabel.textContent = 'Options';
        scroll.appendChild(resetLabel);
        const resetGroup = document.createElement('div');
        resetGroup.className = 'ugc-ios-filter-group';
        const resetRow = document.createElement('button');
        resetRow.type = 'button';
        resetRow.className = 'ugc-ios-filter-row ugc-ios-reset-row';
        resetRow.textContent = 'Reset all filters';
        resetRow.addEventListener('click', resetAll);
        resetGroup.appendChild(resetRow);
        scroll.appendChild(resetGroup);

        const note = document.createElement('div');
        note.className = 'ugc-ios-count-note';
        note.id = 'ugc-ios-filter-result-note';
        scroll.appendChild(note);
        syncResultNote();
    }

    function syncRows() {
        controls.forEach((config) => {
            const control = document.getElementById(config.id);
            if (!control) return;
            sheet.querySelectorAll(`.ugc-ios-filter-row[data-control="${config.id}"]`).forEach((row) => {
                row.classList.toggle('selected', String(row.dataset.value) === String(control.value));
            });
        });
        syncTrigger();
        syncResultNote();
    }

    function syncResultNote() {
        const note = document.getElementById('ugc-ios-filter-result-note');
        if (!note) return;
        const cards = Array.from(document.querySelectorAll('#ugc-card-grid .ugc-card'));
        const visible = cards.filter((card) => !card.classList.contains('hidden') && !card.classList.contains('ugc-relevance-hidden') && card.style.display !== 'none').length;
        note.textContent = `${visible} item${visible === 1 ? '' : 's'} shown`;
    }

    function resetAll() {
        controls.forEach((config) => {
            const control = document.getElementById(config.id);
            if (!control) return;
            control.value = config.defaultValue !== undefined ? config.defaultValue : '';
            dispatch(control);
        });
        syncRows();
    }

    function open() {
        rebuild();
        backdrop.classList.add('open');
        sheet.classList.add('open');
        document.body.style.overflow = 'hidden';
        trigger.setAttribute('aria-expanded', 'true');
    }

    function close() {
        backdrop.classList.remove('open');
        sheet.classList.remove('open');
        document.body.style.overflow = '';
        trigger.setAttribute('aria-expanded', 'false');
    }

    trigger.addEventListener('click', open);
    backdrop.addEventListener('click', close);
    sheet.querySelector('[data-action="done"]').addEventListener('click', close);
    sheet.querySelector('[data-action="reset"]').addEventListener('click', resetAll);

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && sheet.classList.contains('open')) close();
    });
    document.addEventListener('ugc:filters-changed', () => window.setTimeout(syncRows, 0));
    panel.addEventListener('change', () => window.setTimeout(syncRows, 0));

    let startY = null;
    sheet.addEventListener('touchstart', (event) => {
        if (event.touches.length === 1 && event.target.closest('.ugc-ios-filter-handle-wrap')) startY = event.touches[0].clientY;
    }, { passive:true });
    sheet.addEventListener('touchend', (event) => {
        if (startY === null || !event.changedTouches.length) return;
        if (event.changedTouches[0].clientY - startY > 60) close();
        startY = null;
    }, { passive:true });

    const observer = new MutationObserver(() => {
        syncTrigger();
        if (sheet.classList.contains('open')) syncRows();
    });
    observer.observe(panel, { childList:true, subtree:true });
    syncTrigger();
})();