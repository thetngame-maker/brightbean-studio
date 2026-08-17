(function () {
    const grid = document.getElementById('ugc-card-grid');
    if (!grid) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    const reelCards = cards.filter((card) => card.querySelector('video'));
    if (!reelCards.length) return;

    let modal = document.getElementById('ugc-reel-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'ugc-reel-modal';
        modal.className = 'hidden fixed inset-0 z-[340] bg-black/90 backdrop-blur-sm p-4 sm:p-8 items-center justify-center';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.innerHTML = `
            <div class="relative w-full max-w-5xl max-h-full flex flex-col rounded-2xl bg-stone-950 border border-white/10 shadow-2xl overflow-hidden">
                <div class="flex items-center justify-between gap-4 px-4 py-3 border-b border-white/10 text-white">
                    <div class="min-w-0">
                        <div id="ugc-reel-title" class="text-sm font-semibold truncate">Instagram Reel</div>
                        <div id="ugc-reel-contributor" class="text-xs text-stone-400 truncate mt-0.5"></div>
                    </div>
                    <button id="ugc-reel-close" type="button" class="flex-shrink-0 w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 text-white text-xl leading-none flex items-center justify-center" aria-label="Close Reel">×</button>
                </div>
                <div class="min-h-0 flex-1 flex items-center justify-center bg-black p-3 sm:p-5">
                    <video id="ugc-reel-player" controls playsinline preload="metadata" class="max-w-full max-h-[calc(100vh-9rem)] rounded-lg bg-black"></video>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }

    const player = document.getElementById('ugc-reel-player');
    const title = document.getElementById('ugc-reel-title');
    const contributor = document.getElementById('ugc-reel-contributor');
    const closeButton = document.getElementById('ugc-reel-close');
    let previousFocus = null;

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
        if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
    }

    function openReel(button, card, video) {
        if (!modal || !player) return;
        const source = video.currentSrc || (video.querySelector('source') ? video.querySelector('source').src : '') || video.src;
        if (!source) return;
        previousFocus = button;
        player.src = source;
        const heading = card.querySelector('h2');
        const contributorLine = heading ? heading.nextElementSibling : null;
        title.textContent = heading ? (heading.textContent || '').trim() : 'Instagram Reel';
        contributor.textContent = contributorLine ? (contributorLine.textContent || '').trim() : '';
        modal.classList.remove('hidden');
        modal.classList.add('flex');
        document.body.style.overflow = 'hidden';
        player.load();
        window.setTimeout(() => player.play().catch(() => {}), 80);
        if (closeButton) closeButton.focus();
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
        if (badgeCandidates[0]) badgeCandidates[0].textContent = 'Reel';

        if (mediaWrap && !mediaWrap.querySelector('.ugc-reel-open')) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'ugc-reel-open absolute right-2 top-2 z-10 inline-flex items-center gap-1.5 rounded-full bg-black/70 px-2.5 py-1.5 text-[10px] font-semibold text-white backdrop-blur-sm hover:bg-black/85';
            button.innerHTML = '<span aria-hidden="true">▶</span><span>Open Reel</span>';
            button.setAttribute('aria-label', 'Open Reel in large player');
            mediaWrap.appendChild(button);
            button.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                openReel(button, card, video);
            });
        }
    });

    if (closeButton) closeButton.addEventListener('click', closeReel);
    if (modal) modal.addEventListener('click', (event) => { if (event.target === modal) closeReel(); });
    document.addEventListener('keydown', (event) => {
        if (!modal || modal.classList.contains('hidden')) return;
        if (event.key === 'Escape') closeReel();
    });
})();
