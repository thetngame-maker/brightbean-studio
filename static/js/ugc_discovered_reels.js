(function () {
    const grid = document.getElementById('ugc-card-grid');
    if (!grid) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    const reelCards = cards.filter((card) => card.querySelector('video'));
    if (!reelCards.length) return;

    function contributorFor(card) {
        const heading = card.querySelector('h2');
        const line = heading ? heading.nextElementSibling : null;
        return line ? (line.textContent || '').split('·')[0].trim() : '';
    }

    function titleFor(card) {
        const heading = card.querySelector('h2');
        return heading ? (heading.textContent || '').trim() : 'Instagram Reel';
    }

    function videoSourceFor(card) {
        const video = card.querySelector('video');
        if (!video) return '';
        const source = video.querySelector('source');
        return (source && source.src) || video.currentSrc || video.src || '';
    }

    let modal = document.getElementById('ugc-reel-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'ugc-reel-modal';
        modal.className = 'hidden fixed inset-0 z-[340] bg-black/90 backdrop-blur-sm p-3 sm:p-8 items-center justify-center';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.innerHTML = `
            <div class="relative w-full max-w-5xl max-h-full flex flex-col rounded-2xl bg-stone-950 border border-white/10 shadow-2xl overflow-hidden">
                <div class="flex items-center justify-between gap-4 px-4 py-3 border-b border-white/10 text-white">
                    <div class="min-w-0">
                        <div class="flex items-center gap-2 min-w-0">
                            <span class="inline-flex items-center gap-1 rounded-full bg-pink-500/15 px-2 py-1 text-[10px] font-semibold text-pink-300">▶ Reel</span>
                            <div id="ugc-reel-title" class="text-sm font-semibold truncate">Instagram Reel</div>
                            <span id="ugc-reel-counter-desktop" class="hidden sm:inline text-[11px] text-stone-400 whitespace-nowrap"></span>
                        </div>
                        <div id="ugc-reel-contributor" class="text-xs text-stone-400 truncate mt-1"></div>
                    </div>
                    <button id="ugc-reel-close" type="button" class="flex-shrink-0 w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 text-white text-xl leading-none flex items-center justify-center" aria-label="Close Reel">×</button>
                </div>
                <div class="relative min-h-0 flex-1 flex items-center justify-center bg-black p-2 sm:p-4">
                    <button id="ugc-reel-prev" type="button" class="hidden sm:flex absolute left-3 z-10 w-10 h-10 items-center justify-center rounded-full bg-black/60 hover:bg-black/80 border border-white/10 text-white text-2xl" aria-label="Previous Reel">‹</button>
                    <video id="ugc-reel-player" controls playsinline preload="metadata" class="max-w-full max-h-[calc(100vh-10rem)] rounded-lg bg-black"></video>
                    <button id="ugc-reel-next" type="button" class="hidden sm:flex absolute right-3 z-10 w-10 h-10 items-center justify-center rounded-full bg-black/60 hover:bg-black/80 border border-white/10 text-white text-2xl" aria-label="Next Reel">›</button>
                </div>
                <div class="flex sm:hidden items-center justify-between gap-3 px-4 py-3 border-t border-white/10 bg-stone-950">
                    <button id="ugc-reel-prev-mobile" type="button" class="px-3 py-2 rounded-lg bg-white/10 text-white text-xs font-semibold">← Previous</button>
                    <span id="ugc-reel-counter" class="text-[11px] text-stone-400"></span>
                    <button id="ugc-reel-next-mobile" type="button" class="px-3 py-2 rounded-lg bg-white/10 text-white text-xs font-semibold">Next →</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }

    const player = modal.querySelector('#ugc-reel-player');
    const title = modal.querySelector('#ugc-reel-title');
    const contributor = modal.querySelector('#ugc-reel-contributor');
    const counter = modal.querySelector('#ugc-reel-counter');
    const desktopCounter = modal.querySelector('#ugc-reel-counter-desktop');
    const closeButton = modal.querySelector('#ugc-reel-close');
    const prevButton = modal.querySelector('#ugc-reel-prev');
    const nextButton = modal.querySelector('#ugc-reel-next');
    const prevMobile = modal.querySelector('#ugc-reel-prev-mobile');
    const nextMobile = modal.querySelector('#ugc-reel-next-mobile');
    let previousFocus = null;
    let currentCard = null;

    function visibleReelCards() {
        return reelCards.filter((card) => !card.classList.contains('hidden') && card.style.display !== 'none');
    }

    function updateNavigation() {
        const visible = visibleReelCards();
        const index = currentCard ? visible.indexOf(currentCard) : -1;
        const label = index >= 0 ? `${index + 1} of ${visible.length}` : '';
        if (counter) counter.textContent = label;
        if (desktopCounter) desktopCounter.textContent = label;
        const many = visible.length > 1;
        [prevButton, nextButton, prevMobile, nextMobile].forEach((button) => {
            if (button) button.classList.toggle('invisible', !many);
        });
    }

    function render(card) {
        const source = videoSourceFor(card);
        if (!source || !player) return false;
        player.pause();
        player.removeAttribute('src');
        player.load();
        player.src = source;
        title.textContent = titleFor(card);
        contributor.textContent = contributorFor(card);
        currentCard = card;
        updateNavigation();
        player.load();
        return true;
    }

    function openReel(button, card) {
        if (!render(card)) return;
        previousFocus = button;
        modal.classList.remove('hidden');
        modal.classList.add('flex');
        document.body.style.overflow = 'hidden';
        window.setTimeout(() => player.play().catch(() => {}), 80);
        if (closeButton) closeButton.focus();
    }

    function closeReel() {
        if (!modal || modal.classList.contains('hidden')) return;
        if (player) {
            player.pause();
            player.removeAttribute('src');
            player.load();
        }
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        document.body.style.overflow = '';
        currentCard = null;
        if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
    }

    function step(direction) {
        const visible = visibleReelCards();
        if (visible.length < 2) return;
        let index = visible.indexOf(currentCard);
        if (index < 0) index = 0;
        const nextCard = visible[(index + direction + visible.length) % visible.length];
        if (render(nextCard)) window.setTimeout(() => player.play().catch(() => {}), 60);
    }

    reelCards.forEach((card) => {
        const video = card.querySelector('video');
        if (!video) return;
        video.setAttribute('playsinline', '');
        video.setAttribute('preload', 'metadata');

        const mediaWrap = video.parentElement;
        if (mediaWrap) mediaWrap.classList.add('relative');

        const badgeCandidates = Array.from(card.querySelectorAll('span')).filter((node) => {
            const text = (node.textContent || '').trim().toLowerCase();
            return text === 'photo' || text === 'community post';
        });
        if (badgeCandidates[0]) {
            badgeCandidates[0].innerHTML = '<span aria-hidden="true">▶</span> Reel';
            badgeCandidates[0].classList.add('gap-1', 'bg-pink-50', 'text-pink-700');
        }

        if (mediaWrap && !mediaWrap.querySelector('.ugc-reel-open')) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'ugc-reel-open absolute right-2 top-2 z-10 inline-flex items-center gap-1.5 rounded-full bg-black/70 px-2.5 py-1.5 text-[10px] font-semibold text-white backdrop-blur-sm hover:bg-black/85';
            button.innerHTML = '<span aria-hidden="true">▶</span><span>Open Reel</span>';
            button.setAttribute('aria-label', `Open Reel: ${titleFor(card)}`);
            mediaWrap.appendChild(button);
            button.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                openReel(button, card);
            });
        }
    });

    if (closeButton) closeButton.addEventListener('click', closeReel);
    if (prevButton) prevButton.addEventListener('click', () => step(-1));
    if (nextButton) nextButton.addEventListener('click', () => step(1));
    if (prevMobile) prevMobile.addEventListener('click', () => step(-1));
    if (nextMobile) nextMobile.addEventListener('click', () => step(1));
    if (modal) modal.addEventListener('click', (event) => { if (event.target === modal) closeReel(); });
    document.addEventListener('keydown', (event) => {
        if (!modal || modal.classList.contains('hidden')) return;
        if (event.key === 'Escape') closeReel();
        if (event.key === 'ArrowLeft') { event.preventDefault(); step(-1); }
        if (event.key === 'ArrowRight') { event.preventDefault(); step(1); }
    });
})();
