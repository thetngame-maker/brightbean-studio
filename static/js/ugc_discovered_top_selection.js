(function () {
    const grid = document.getElementById('ugc-card-grid');
    const toolbar = document.getElementById('ugc-bulk-permission-form');
    if (!grid || !toolbar) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    if (!cards.length) return;

    const scores = new Map();
    let button = document.getElementById('ugc-select-top-ten');

    function submissionIdFor(card) {
        return String(card.dataset.submissionId || '').trim();
    }

    function isVisible(card) {
        return !card.classList.contains('hidden') && card.style.display !== 'none';
    }

    function checkboxFor(card) {
        return card.querySelector('.ugc-select-item');
    }

    function scoreFor(card) {
        const id = submissionIdFor(card);
        if (id && scores.has(id)) return Number(scores.get(id) || 0);
        return Number(card.dataset.engagement || 0);
    }

    function visibleSelectableCards() {
        return cards.filter((card) => isVisible(card) && checkboxFor(card));
    }

    function syncSelectionUI() {
        const firstCheckbox = cards.map(checkboxFor).find(Boolean);
        if (firstCheckbox) firstCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function selectTopTen() {
        const visible = visibleSelectableCards();
        if (!visible.length) return;

        const ranked = visible.slice().sort((a, b) => {
            const scoreDiff = scoreFor(b) - scoreFor(a);
            if (scoreDiff) return scoreDiff;
            return Number(b.dataset.submitted || 0) - Number(a.dataset.submitted || 0);
        });
        const winners = new Set(ranked.slice(0, 10));

        cards.forEach((card) => {
            const checkbox = checkboxFor(card);
            if (!checkbox) return;
            checkbox.checked = winners.has(card);
        });
        syncSelectionUI();

        const original = button.textContent;
        button.textContent = `Top ${Math.min(10, winners.size)} selected`;
        button.classList.add('bg-violet-100', 'text-violet-800', 'border-violet-300');
        window.setTimeout(() => {
            button.textContent = original;
            button.classList.remove('bg-violet-100', 'text-violet-800', 'border-violet-300');
        }, 1800);
    }

    function ensureButton() {
        if (button) return;
        const leftGroup = toolbar.querySelector('.flex.items-center.gap-3.min-w-0');
        if (!leftGroup) return;
        button = document.createElement('button');
        button.type = 'button';
        button.id = 'ugc-select-top-ten';
        button.className = 'inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-violet-200 bg-white text-[11px] font-semibold text-violet-700 hover:bg-violet-50 whitespace-nowrap';
        button.innerHTML = '<span aria-hidden="true">★</span><span>Select top 10</span>';
        button.title = 'Select the 10 highest-engagement items from the current filtered view';
        button.addEventListener('click', selectTopTen);
        leftGroup.appendChild(button);
    }

    ensureButton();

    const intelligenceUrl = window.location.pathname.replace(/\/?$/, '/discovered/intelligence/');
    fetch(intelligenceUrl, {
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin',
        cache: 'no-store'
    })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error('Discovery intelligence unavailable')))
        .then((payload) => {
            (payload.items || []).forEach((item) => {
                const id = String(item.id || '');
                if (!id) return;
                const score = Number(item.engagement_score || 0);
                scores.set(id, Number.isFinite(score) ? score : 0);
            });
        })
        .catch(() => {});
})();
