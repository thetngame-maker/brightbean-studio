(function () {
    const grid = document.getElementById('ugc-card-grid');
    if (!grid) return;
    const discoveredTab = document.querySelector('a[href*="tab=discovered"].border-violet-500');
    if (!discoveredTab) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    if (!cards.length) return;

    if (!document.getElementById('ugc-relevance-style')) {
        const style = document.createElement('style');
        style.id = 'ugc-relevance-style';
        style.textContent = '.ugc-card.ugc-relevance-hidden{display:none!important}';
        document.head.appendChild(style);
    }

    const sortSelect = document.getElementById('ugc-sort');
    const filterPanel = sortSelect ? sortSelect.parentElement : null;
    if (!filterPanel) return;

    let select = document.getElementById('ugc-relevance-filter');
    if (!select) {
        select = document.createElement('select');
        select.id = 'ugc-relevance-filter';
        select.className = 'h-9 text-xs border border-stone-200 rounded-lg bg-white px-3 text-stone-700 outline-none focus:border-violet-400';
        select.setAttribute('aria-label', 'Discovery relevance');
        select.innerHTML = `
            <option value="relevant">Relevant only</option>
            <option value="all">All relevance</option>
            <option value="strong">Strong relevance</option>
            <option value="possible">Possible relevance</option>
            <option value="low">Low relevance</option>
        `;
        filterPanel.insertBefore(select, sortSelect);
    }

    function badgeFor(card, item) {
        if (card.querySelector('.ugc-relevance-badge')) return;
        const status = String(item.relevance_status || 'possible');
        if (status === 'possible') return;
        const intelligence = card.querySelector('.ugc-discovery-intelligence');
        const host = intelligence || card.querySelector('h2')?.parentElement;
        if (!host) return;
        const badge = document.createElement('span');
        badge.className = 'ugc-relevance-badge inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-semibold ' +
            (status === 'strong' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-800');
        badge.textContent = status === 'strong' ? '✓ Strong match' : '⚠ Low relevance';
        badge.title = item.relevance_reason || '';
        if (intelligence) intelligence.appendChild(badge);
        else host.appendChild(badge);
    }

    function apply() {
        const value = select.value || 'relevant';
        cards.forEach((card) => {
            const status = card.dataset.relevanceStatus || 'possible';
            let show = true;
            if (value === 'relevant') show = status !== 'low';
            else if (value !== 'all') show = status === value;
            card.classList.toggle('ugc-relevance-hidden', !show);
        });
        document.dispatchEvent(new CustomEvent('ugc:filters-changed'));
    }

    select.addEventListener('change', apply);

    const url = window.location.pathname.replace(/\/?$/, '/discovered/intelligence/');
    fetch(url, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin', cache: 'no-store' })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error('Relevance unavailable')))
        .then((payload) => {
            const byId = new Map((payload.items || []).map((item) => [String(item.id || ''), item]));
            cards.forEach((card) => {
                const id = String(card.dataset.submissionId || '').trim();
                const item = byId.get(id);
                if (!item) return;
                card.dataset.relevanceStatus = String(item.relevance_status || 'possible');
                card.dataset.relevanceScore = String(item.relevance_score || 0);
                badgeFor(card, item);
            });
            apply();
        })
        .catch(() => {});
})();
