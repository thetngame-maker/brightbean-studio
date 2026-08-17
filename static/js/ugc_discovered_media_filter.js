(function () {
    const grid = document.getElementById('ugc-card-grid');
    const sortSelect = document.getElementById('ugc-sort');
    if (!grid || !sortSelect || !sortSelect.parentNode) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    if (!cards.length) return;

    cards.forEach((card) => {
        card.dataset.mediaType = card.querySelector('video') ? 'reel' : 'photo';
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

    function mediaMatches(card) {
        return !mediaSelect.value || card.dataset.mediaType === mediaSelect.value;
    }

    function applyMediaFilter() {
        cards.forEach((card) => {
            const matches = mediaMatches(card);
            card.classList.toggle('ugc-media-filtered-out', !matches);
            if (!matches) {
                card.style.setProperty('display', 'none', 'important');
            } else if (card.classList.contains('ugc-media-filtered-out')) {
                card.style.removeProperty('display');
            } else {
                card.style.removeProperty('display');
            }
        });

        // Permission filtering also uses inline display. Reapply our media rule
        // after its handlers finish so the two filters compose instead of fight.
        window.requestAnimationFrame(() => {
            cards.forEach((card) => {
                if (!mediaMatches(card)) card.style.setProperty('display', 'none', 'important');
            });
            const resultCount = document.getElementById('ugc-result-count');
            if (resultCount) {
                const visible = cards.filter((card) => !card.classList.contains('hidden') && card.style.display !== 'none').length;
                const filtering = Boolean(mediaSelect.value || document.getElementById('ugc-permission-filter')?.value || document.getElementById('ugc-source')?.value || document.getElementById('ugc-search')?.value.trim());
                resultCount.textContent = filtering ? `${visible} of ${cards.length}` : `${cards.length} item${cards.length === 1 ? '' : 's'}`;
            }
        });
    }

    mediaSelect.addEventListener('change', applyMediaFilter);

    ['ugc-permission-filter', 'ugc-source', 'ugc-sort', 'ugc-search-submit', 'ugc-search-clear', 'ugc-empty-clear'].forEach((id) => {
        const control = document.getElementById(id);
        if (!control) return;
        const eventName = control.tagName === 'SELECT' ? 'change' : 'click';
        control.addEventListener(eventName, () => window.setTimeout(applyMediaFilter, 0));
    });

    const search = document.getElementById('ugc-search');
    if (search) {
        search.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') window.setTimeout(applyMediaFilter, 0);
        });
    }

    applyMediaFilter();
})();
