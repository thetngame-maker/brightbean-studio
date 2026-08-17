(function () {
    const grid = document.getElementById('ugc-card-grid');
    const sortSelect = document.getElementById('ugc-sort');
    if (!grid || !sortSelect || !sortSelect.parentNode) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    if (!cards.length) return;

    cards.forEach((card) => {
        card.dataset.mediaType = card.querySelector('video') ? 'reel' : 'photo';
        card.dataset.discoveryMethod = card.dataset.discoveryMethod || '';
    });

    let mediaSelect = document.getElementById('ugc-media-filter');
    if (!mediaSelect) {
        mediaSelect = document.createElement('select');
        mediaSelect.id = 'ugc-media-filter';
        mediaSelect.className = 'h-9 text-xs border border-stone-200 rounded-lg bg-white px-3 text-stone-700 outline-none focus:border-violet-400';
        mediaSelect.setAttribute('aria-label', 'Media type');
        mediaSelect.innerHTML = `
            <option value="">All media</option>
            <option value="photo">Photos</option>
            <option value="reel">Reels & videos</option>
        `;
        sortSelect.parentNode.insertBefore(mediaSelect, sortSelect);
    }

    let methodSelect = document.getElementById('ugc-discovery-method-filter');
    if (!methodSelect) {
        methodSelect = document.createElement('select');
        methodSelect.id = 'ugc-discovery-method-filter';
        methodSelect.className = 'h-9 text-xs border border-stone-200 rounded-lg bg-white px-3 text-stone-700 outline-none focus:border-violet-400';
        methodSelect.setAttribute('aria-label', 'Discovery method');
        methodSelect.innerHTML = `
            <option value="">All discovery methods</option>
            <option value="keyword">Keyword</option>
            <option value="hashtag">Hashtag</option>
            <option value="location">Location</option>
            <option value="account">Account</option>
        `;
        sortSelect.parentNode.insertBefore(methodSelect, sortSelect);
    }

    const METHOD_LABELS = {
        keyword: 'Keyword',
        hashtag: 'Hashtag',
        location: 'Location',
        account: 'Account'
    };

    const METHOD_CLASSES = {
        keyword: 'bg-fuchsia-50 text-fuchsia-700',
        hashtag: 'bg-sky-50 text-sky-700',
        location: 'bg-emerald-50 text-emerald-700',
        account: 'bg-amber-50 text-amber-700'
    };

    function addMethodBadge(card, method) {
        if (!METHOD_LABELS[method] || card.querySelector('.ugc-discovery-method-badge')) return;
        const intelligence = card.querySelector('.ugc-discovery-intelligence');
        const title = card.querySelector('h2');
        const contributor = title ? title.nextElementSibling : null;
        const badge = document.createElement('span');
        badge.className = `ugc-discovery-method-badge inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${METHOD_CLASSES[method]}`;
        badge.textContent = METHOD_LABELS[method];
        badge.title = `Discovered by ${METHOD_LABELS[method]} search`;
        if (intelligence) intelligence.insertBefore(badge, intelligence.firstChild);
        else if (contributor) {
            const row = document.createElement('div');
            row.className = 'ugc-discovery-method-row mt-2 flex flex-wrap items-center gap-1.5';
            row.appendChild(badge);
            contributor.insertAdjacentElement('afterend', row);
        }
    }

    function mediaMatches(card) {
        return !mediaSelect.value || card.dataset.mediaType === mediaSelect.value;
    }

    function methodMatches(card) {
        return !methodSelect.value || card.dataset.discoveryMethod === methodSelect.value;
    }

    function combinedMatches(card) {
        return mediaMatches(card) && methodMatches(card);
    }

    function applyLibraryFilters() {
        cards.forEach((card) => {
            const matches = combinedMatches(card);
            card.classList.toggle('ugc-library-filtered-out', !matches);
            if (!matches) card.style.setProperty('display', 'none', 'important');
            else card.style.removeProperty('display');
        });

        window.requestAnimationFrame(() => {
            cards.forEach((card) => {
                if (!combinedMatches(card)) card.style.setProperty('display', 'none', 'important');
            });
            const resultCount = document.getElementById('ugc-result-count');
            if (resultCount) {
                const visible = cards.filter((card) => !card.classList.contains('hidden') && card.style.display !== 'none').length;
                const filtering = Boolean(
                    mediaSelect.value ||
                    methodSelect.value ||
                    document.getElementById('ugc-permission-filter')?.value ||
                    document.getElementById('ugc-source')?.value ||
                    document.getElementById('ugc-search')?.value.trim()
                );
                resultCount.textContent = filtering ? `${visible} of ${cards.length}` : `${cards.length} item${cards.length === 1 ? '' : 's'}`;
            }
        });
    }

    function applyIntelligence(payload) {
        const byId = new Map();
        cards.forEach((card) => {
            if (card.dataset.submissionId) byId.set(card.dataset.submissionId, card);
        });
        (payload.items || []).forEach((item) => {
            const card = byId.get(String(item.id || ''));
            if (!card) return;
            const method = String(item.discovery_method || '').toLowerCase();
            if (!METHOD_LABELS[method]) return;
            card.dataset.discoveryMethod = method;
            addMethodBadge(card, method);
        });
        applyLibraryFilters();
    }

    mediaSelect.addEventListener('change', applyLibraryFilters);
    methodSelect.addEventListener('change', applyLibraryFilters);

    ['ugc-permission-filter', 'ugc-source', 'ugc-sort', 'ugc-search-submit', 'ugc-search-clear', 'ugc-empty-clear'].forEach((id) => {
        const control = document.getElementById(id);
        if (!control) return;
        const eventName = control.tagName === 'SELECT' ? 'change' : 'click';
        control.addEventListener(eventName, () => window.setTimeout(applyLibraryFilters, 0));
    });

    const search = document.getElementById('ugc-search');
    if (search) {
        search.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') window.setTimeout(applyLibraryFilters, 0);
        });
    }

    const intelligenceUrl = window.location.pathname.replace(/\/?$/, '/discovered/intelligence/');
    fetch(intelligenceUrl, {
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin',
        cache: 'no-store'
    })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error('Discovery intelligence unavailable')))
        .then(applyIntelligence)
        .catch(() => applyLibraryFilters());

    applyLibraryFilters();

    if (!document.querySelector('script[data-ugc-discovery-performance]')) {
        const performanceScript = document.createElement('script');
        performanceScript.src = '/static/js/ugc_discovered_performance.js';
        performanceScript.dataset.ugcDiscoveryPerformance = '1';
        if (document.currentScript && document.currentScript.nonce) performanceScript.nonce = document.currentScript.nonce;
        document.head.appendChild(performanceScript);
    }
})();
