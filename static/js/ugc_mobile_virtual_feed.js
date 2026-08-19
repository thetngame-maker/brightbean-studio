(function () {
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    if (!document.body.classList.contains('ugc-mobile-community')) return;

    const grid = document.getElementById('ugc-card-grid');
    if (!grid || grid.dataset.virtualFeedReady === '1') return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    /* Server-side mobile paging now sends 16 cards. Do not virtualize an
       already-small page; virtualization remains as a fallback for older or
       non-paged responses. */
    if (cards.length <= 20) return;

    grid.dataset.virtualFeedReady = '1';
    const BATCH_SIZE = 12;
    let rendered = 0;
    let loading = false;

    const sentinel = document.createElement('div');
    sentinel.id = 'ugc-mobile-virtual-sentinel';
    sentinel.style.cssText = 'grid-column:1/-1;display:flex;justify-content:center;padding:14px 0 calc(24px + env(safe-area-inset-bottom));color:#8e8e93;font-size:12px;';
    sentinel.innerHTML = '<button type="button" style="border:0;background:#f2f2f7;border-radius:999px;padding:10px 16px;color:#6d28d9;font:600 13px system-ui,-apple-system,BlinkMacSystemFont,sans-serif;">Load more</button>';

    function eligible(card) {
        return !card.classList.contains('hidden') &&
            !card.classList.contains('ugc-relevance-hidden') &&
            !card.classList.contains('ugc-library-filtered-out') &&
            card.style.display !== 'none';
    }

    function detachAllCards() {
        cards.forEach((card) => {
            if (card.parentNode === grid) grid.removeChild(card);
        });
        if (sentinel.parentNode === grid) grid.removeChild(sentinel);
    }

    function updateSentinel() {
        const remaining = cards.some((card) => !card.isConnected && eligible(card));
        if (remaining) {
            if (sentinel.parentNode !== grid) grid.appendChild(sentinel);
            const button = sentinel.querySelector('button');
            const visibleNow = cards.filter((card) => card.isConnected && eligible(card)).length;
            if (button) button.textContent = `Load more · ${visibleNow} shown`;
        } else if (sentinel.parentNode === grid) {
            grid.removeChild(sentinel);
        }
    }

    function hydrateVideo(card) {
        card.querySelectorAll('video[data-ugc-deferred-video], video[preload="none"]').forEach((video) => {
            if (video.dataset.ugcVirtualHydrated === '1') return;
            video.dataset.ugcVirtualHydrated = '1';
            if (video.preload === 'none') video.preload = 'metadata';
            try { video.load(); } catch (error) { /* no-op */ }
        });
    }

    function appendBatch(limit) {
        if (loading) return;
        loading = true;
        let added = 0;
        for (const card of cards) {
            if (added >= limit) break;
            if (card.isConnected || !eligible(card)) continue;
            grid.insertBefore(card, sentinel.parentNode === grid ? sentinel : null);
            hydrateVideo(card);
            added += 1;
            rendered += 1;
        }
        updateSentinel();
        document.dispatchEvent(new CustomEvent('ugc:virtual-feed-changed', { detail: { added, rendered } }));
        loading = false;
    }

    function resetWindow() {
        const y = window.scrollY;
        detachAllCards();
        rendered = 0;
        appendBatch(BATCH_SIZE);
        if (window.scrollY > y + 20) window.scrollTo({ top: y, behavior: 'auto' });
    }

    sentinel.querySelector('button').addEventListener('click', () => appendBatch(BATCH_SIZE));

    const intersection = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) appendBatch(BATCH_SIZE);
    }, { rootMargin: '700px 0px' });
    intersection.observe(sentinel);

    let resetTimer = null;
    function scheduleReset() {
        window.clearTimeout(resetTimer);
        resetTimer = window.setTimeout(resetWindow, 80);
    }

    document.addEventListener('ugc:filters-changed', scheduleReset);
    ['ugc-search-submit', 'ugc-search-clear', 'ugc-empty-clear'].forEach((id) => {
        const node = document.getElementById(id);
        if (node) node.addEventListener('click', () => window.setTimeout(scheduleReset, 0));
    });
    const search = document.getElementById('ugc-search');
    if (search) search.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') window.setTimeout(scheduleReset, 0);
    });

    window.setTimeout(resetWindow, 350);
})();