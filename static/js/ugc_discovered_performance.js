(function () {
    const grid = document.getElementById('ugc-card-grid');
    const bulkToolbar = document.getElementById('ugc-bulk-permission-form');
    if (!grid || !bulkToolbar) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    if (!cards.length) return;

    const metricsById = new Map();
    let refreshTimer = null;

    function compact(value) {
        const number = Number(value || 0);
        if (!Number.isFinite(number)) return '0';
        if (number >= 1000000) return `${(number / 1000000).toFixed(number >= 10000000 ? 0 : 1)}M`;
        if (number >= 1000) return `${(number / 1000).toFixed(number >= 10000 ? 0 : 1)}K`;
        return Math.round(number).toLocaleString();
    }

    function average(values) {
        if (!values.length) return 0;
        return values.reduce((sum, value) => sum + Number(value || 0), 0) / values.length;
    }

    function isVisible(card) {
        return !card.classList.contains('hidden') && card.style.display !== 'none';
    }

    function activeScopeLabel() {
        const method = document.getElementById('ugc-discovery-method-filter');
        const media = document.getElementById('ugc-media-filter');
        const source = document.getElementById('ugc-source');
        const permission = document.getElementById('ugc-permission-filter');
        const parts = [];
        [method, media, source, permission].forEach((select) => {
            if (!select || !select.value) return;
            const option = select.options && select.options[select.selectedIndex];
            if (option && option.textContent) parts.push(option.textContent.trim());
        });
        return parts.length ? parts.join(' · ') : 'All discovered content';
    }

    let strip = document.getElementById('ugc-discovery-performance');
    if (!strip) {
        strip = document.createElement('section');
        strip.id = 'ugc-discovery-performance';
        strip.className = 'mb-3 rounded-xl border border-stone-200 bg-white px-3 py-2.5 shadow-sm';
        strip.innerHTML = `
            <div class="flex flex-col lg:flex-row lg:items-center gap-2.5">
                <div class="min-w-[155px]">
                    <div class="text-[10px] font-semibold uppercase tracking-wide text-stone-400">Discovery performance</div>
                    <div id="ugc-performance-scope" class="text-xs font-semibold text-stone-700 mt-0.5 truncate">All discovered content</div>
                </div>
                <div class="grid grid-cols-3 sm:grid-cols-6 gap-1.5 flex-1">
                    <div class="rounded-lg bg-stone-50 px-2.5 py-2"><div class="text-[9px] uppercase tracking-wide text-stone-400">Results</div><div id="ugc-performance-results" class="text-sm font-semibold text-stone-800 mt-0.5">—</div></div>
                    <div class="rounded-lg bg-pink-50 px-2.5 py-2"><div class="text-[9px] uppercase tracking-wide text-pink-500">Reels</div><div id="ugc-performance-reels" class="text-sm font-semibold text-pink-800 mt-0.5">—</div></div>
                    <div class="rounded-lg bg-rose-50 px-2.5 py-2"><div class="text-[9px] uppercase tracking-wide text-rose-500">Avg likes</div><div id="ugc-performance-likes" class="text-sm font-semibold text-rose-800 mt-0.5">—</div></div>
                    <div class="rounded-lg bg-sky-50 px-2.5 py-2"><div class="text-[9px] uppercase tracking-wide text-sky-500">Avg comments</div><div id="ugc-performance-comments" class="text-sm font-semibold text-sky-800 mt-0.5">—</div></div>
                    <div class="rounded-lg bg-violet-50 px-2.5 py-2"><div class="text-[9px] uppercase tracking-wide text-violet-500">Avg Reel views</div><div id="ugc-performance-views" class="text-sm font-semibold text-violet-800 mt-0.5">—</div></div>
                    <div class="rounded-lg bg-amber-50 px-2.5 py-2"><div class="text-[9px] uppercase tracking-wide text-amber-600">Avg score</div><div id="ugc-performance-score" class="text-sm font-semibold text-amber-800 mt-0.5">—</div></div>
                </div>
            </div>
        `;
        bulkToolbar.parentNode.insertBefore(strip, bulkToolbar);
    }

    const scopeNode = strip.querySelector('#ugc-performance-scope');
    const resultNode = strip.querySelector('#ugc-performance-results');
    const reelsNode = strip.querySelector('#ugc-performance-reels');
    const likesNode = strip.querySelector('#ugc-performance-likes');
    const commentsNode = strip.querySelector('#ugc-performance-comments');
    const viewsNode = strip.querySelector('#ugc-performance-views');
    const scoreNode = strip.querySelector('#ugc-performance-score');

    function render() {
        const visible = cards.filter(isVisible);
        const rows = visible.map((card) => metricsById.get(card.dataset.submissionId || '') || {});
        const reels = visible.filter((card) => card.querySelector('video'));
        const reelRows = reels.map((card) => metricsById.get(card.dataset.submissionId || '') || {});
        const reelViews = reelRows.map((row) => Number(row.view_count || 0)).filter((value) => value > 0);

        scopeNode.textContent = activeScopeLabel();
        resultNode.textContent = visible.length.toLocaleString();
        reelsNode.textContent = visible.length ? `${reels.length} · ${Math.round((reels.length / visible.length) * 100)}%` : '0 · 0%';
        likesNode.textContent = compact(average(rows.map((row) => row.like_count || 0)));
        commentsNode.textContent = compact(average(rows.map((row) => row.comment_count || 0)));
        viewsNode.textContent = reelViews.length ? compact(average(reelViews)) : '—';
        scoreNode.textContent = compact(average(rows.map((row) => row.engagement_score || 0)));
    }

    function scheduleRender() {
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(render, 45);
    }

    const intelligenceUrl = window.location.pathname.replace(/\/?$/, '/discovered/intelligence/');
    fetch(intelligenceUrl, {
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin',
        cache: 'no-store'
    })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error('Discovery intelligence unavailable')))
        .then((payload) => {
            (payload.items || []).forEach((item) => metricsById.set(String(item.id || ''), item));
            render();
        })
        .catch(render);

    document.addEventListener('change', (event) => {
        if (event.target && event.target.matches('select, input[type="checkbox"]')) scheduleRender();
    });
    document.addEventListener('click', (event) => {
        if (event.target && event.target.closest('#ugc-search-submit, #ugc-search-clear, #ugc-empty-clear')) scheduleRender();
    });
    const search = document.getElementById('ugc-search');
    if (search) search.addEventListener('keydown', (event) => { if (event.key === 'Enter') scheduleRender(); });

    const observer = new MutationObserver(scheduleRender);
    cards.forEach((card) => observer.observe(card, { attributes: true, attributeFilter: ['class', 'style'] }));

    render();
})();
