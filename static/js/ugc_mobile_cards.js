(function () {
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    const grid = document.getElementById('ugc-card-grid');
    if (!grid) return;

    const style = document.createElement('style');
    style.textContent = `
        @media (max-width: 767px) {
            body.ugc-mobile-community { background:#f6f6f8; }
            body.ugc-mobile-community main { background:#f6f6f8; }

            /* Compact iOS-like page chrome. */
            body.ugc-mobile-community .ugc-mobile-page-header { gap:.45rem !important; margin-bottom:.55rem !important; }
            body.ugc-mobile-community .ugc-mobile-page-header h1 { font-size:1.6rem !important; letter-spacing:-.04em; }
            body.ugc-mobile-community .ugc-mobile-page-header p { display:none !important; }
            body.ugc-mobile-community .ugc-mobile-header-actions { position:absolute; right:1rem; top:4.45rem; width:auto !important; display:block !important; }
            body.ugc-mobile-community .ugc-mobile-header-actions > div { display:none !important; }
            body.ugc-mobile-community .ugc-mobile-header-actions > a {
                width:40px !important; height:40px !important; min-height:40px !important; padding:0 !important; border-radius:999px !important;
                font-size:0 !important; box-shadow:none !important;
            }
            body.ugc-mobile-community .ugc-mobile-header-actions > a span { font-size:1.55rem !important; line-height:1 !important; }
            body.ugc-mobile-community .ugc-mobile-tabs { margin-top:.1rem; background:transparent; }
            body.ugc-mobile-community .ugc-mobile-tabs > a { padding:.55rem .75rem !important; font-size:.78rem !important; }

            /* Search/filter controls read like a small iOS toolbar. */
            body.ugc-mobile-community .ugc-mobile-controls-wrap { margin-bottom:.5rem !important; gap:.45rem !important; }
            body.ugc-mobile-community #ugc-search-form { display:none !important; }
            body.ugc-mobile-community.ugc-ios-search-open #ugc-search-form { display:flex !important; }
            body.ugc-mobile-community .ugc-ios-toolbar {
                display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.45rem; margin-bottom:.45rem;
            }
            body.ugc-mobile-community .ugc-ios-toolbar button {
                min-height:40px; border:0; border-radius:12px; background:#fff; color:#3a3a3c; font-size:.72rem; font-weight:700;
                box-shadow:0 1px 0 rgba(0,0,0,.05); display:flex; align-items:center; justify-content:center; gap:.35rem;
            }
            body.ugc-mobile-community .ugc-ios-toolbar button.active { color:#6d28d9; background:#f3efff; }
            body.ugc-mobile-community .ugc-mobile-filter-toggle { display:none !important; }
            body.ugc-mobile-community .ugc-mobile-filter-panel { background:#fff; border-radius:14px; padding:.55rem !important; }
            body.ugc-mobile-community .ugc-mobile-filter-panel.ugc-mobile-filter-panel-closed { padding:0 !important; }
            body.ugc-mobile-community #ugc-discovery-performance { display:none !important; }
            body.ugc-mobile-community.ugc-ios-insights-open #ugc-discovery-performance { display:block !important; }
            body.ugc-mobile-community #ugc-bulk-permission-form { display:none !important; }
            body.ugc-mobile-community.ugc-ios-select-open #ugc-bulk-permission-form { display:flex !important; }

            /* Feed cards: media + the handful of fields needed to decide whether to open. */
            body.ugc-mobile-community #ugc-card-grid { gap:.65rem !important; }
            body.ugc-mobile-community #ugc-card-grid .ugc-card {
                border:0 !important; border-radius:18px !important; box-shadow:0 1px 2px rgba(0,0,0,.05) !important; background:#fff;
            }
            body.ugc-mobile-community #ugc-card-grid .ugc-card > div:first-child[style] { max-height:280px !important; border-bottom:0 !important; }
            body.ugc-mobile-community #ugc-card-grid .ugc-card > div:first-child[style] img,
            body.ugc-mobile-community #ugc-card-grid .ugc-card > div:first-child[style] video { height:280px !important; }
            body.ugc-mobile-community #ugc-card-grid .ugc-card > .p-3\\.5 { padding:.85rem .9rem .9rem !important; }
            body.ugc-mobile-community .ugc-card-select { width:34px !important; height:34px !important; border-radius:11px !important; }

            body.ugc-mobile-community .ugc-card .ugc-ios-feed-hidden { display:none !important; }
            body.ugc-mobile-community .ugc-card .ugc-ios-feed-badges > * { display:none !important; }
            body.ugc-mobile-community .ugc-card .ugc-ios-feed-badges > :first-child { display:inline-flex !important; }
            body.ugc-mobile-community .ugc-card .ugc-ios-feed-badges { margin-bottom:.35rem !important; }
            body.ugc-mobile-community .ugc-card h2 { font-size:1rem !important; line-height:1.2; letter-spacing:-.015em; }
            body.ugc-mobile-community .ugc-card h2 + p { margin-top:.2rem !important; font-size:.72rem !important; }
            body.ugc-mobile-community .ugc-card p.line-clamp-3 { -webkit-line-clamp:2 !important; margin-top:.55rem !important; font-size:.78rem !important; line-height:1.35rem !important; }
            body.ugc-mobile-community .ugc-card .ugc-discovery-intelligence { margin-top:.45rem !important; gap:.35rem !important; }
            body.ugc-mobile-community .ugc-card .ugc-discovery-intelligence span { border-radius:999px !important; padding:.22rem .48rem !important; }
            body.ugc-mobile-community .ugc-ios-card-status {
                display:flex; align-items:center; gap:.4rem; margin-top:.55rem; color:#6e6e73; font-size:.68rem; white-space:nowrap; overflow:hidden;
            }
            body.ugc-mobile-community .ugc-ios-status-pill {
                display:inline-flex; align-items:center; gap:.25rem; max-width:45%; overflow:hidden; text-overflow:ellipsis;
                padding:.25rem .5rem; border-radius:999px; background:#f2f2f7; color:#636366; font-weight:650;
            }
            body.ugc-mobile-community .ugc-ios-status-pill.relevant { background:#ecfdf3; color:#18794e; }
            body.ugc-mobile-community .ugc-ios-status-pill.permission { margin-left:auto; color:#6d28d9; background:#f3efff; }
            body.ugc-mobile-community .ugc-ios-card-open-hint { color:#8e8e93; font-size:.9rem; }

            /* Bottom sheet */
            #ugc-ios-sheet-backdrop {
                position:fixed; inset:0; z-index:390; background:rgba(0,0,0,.28); opacity:0; pointer-events:none; transition:opacity 180ms ease;
            }
            #ugc-ios-sheet-backdrop.open { opacity:1; pointer-events:auto; }
            #ugc-ios-sheet {
                position:fixed; left:0; right:0; bottom:0; z-index:400; max-height:91dvh; background:#f6f6f8;
                border-radius:24px 24px 0 0; transform:translateY(102%); transition:transform 230ms cubic-bezier(.2,.8,.2,1);
                box-shadow:0 -16px 50px rgba(0,0,0,.18); overflow:hidden; padding-bottom:env(safe-area-inset-bottom);
            }
            #ugc-ios-sheet.open { transform:translateY(0); }
            #ugc-ios-sheet .ugc-ios-handle-wrap { height:24px; display:flex; justify-content:center; align-items:center; background:#fff; }
            #ugc-ios-sheet .ugc-ios-handle { width:38px; height:5px; border-radius:999px; background:#c7c7cc; }
            #ugc-ios-sheet .ugc-ios-sheet-scroll { overflow-y:auto; max-height:calc(91dvh - 24px); -webkit-overflow-scrolling:touch; }
            #ugc-ios-sheet .ugc-ios-sheet-header { background:#fff; padding:.35rem 1rem .8rem; display:flex; align-items:flex-start; gap:.75rem; }
            #ugc-ios-sheet .ugc-ios-sheet-title { min-width:0; flex:1; }
            #ugc-ios-sheet .ugc-ios-sheet-title h3 { margin:0; font-size:1.15rem; line-height:1.25; color:#1c1c1e; letter-spacing:-.02em; }
            #ugc-ios-sheet .ugc-ios-sheet-title p { margin:.2rem 0 0; font-size:.75rem; color:#8e8e93; }
            #ugc-ios-sheet .ugc-ios-close { width:32px; height:32px; border:0; border-radius:999px; background:#e9e9ee; color:#636366; font-size:1.2rem; font-weight:700; }
            #ugc-ios-sheet .ugc-ios-media { background:#000; max-height:42dvh; display:flex; align-items:center; justify-content:center; overflow:hidden; }
            #ugc-ios-sheet .ugc-ios-media img, #ugc-ios-sheet .ugc-ios-media video { width:100%; max-height:42dvh; object-fit:contain; background:#000; }
            #ugc-ios-sheet .ugc-ios-section { margin:.7rem .75rem 0; background:#fff; border-radius:16px; overflow:hidden; }
            #ugc-ios-sheet .ugc-ios-section-pad { padding:.85rem .95rem; }
            #ugc-ios-sheet .ugc-ios-caption { font-size:.84rem; line-height:1.35rem; color:#3a3a3c; white-space:pre-line; }
            #ugc-ios-sheet .ugc-ios-sheet-metrics { display:flex; flex-wrap:wrap; gap:.4rem; margin-top:.65rem; }
            #ugc-ios-sheet .ugc-ios-sheet-metrics span { font-size:.7rem; border-radius:999px; padding:.3rem .55rem; background:#f2f2f7; color:#636366; }
            #ugc-ios-sheet .ugc-ios-section-title { font-size:.72rem; font-weight:700; color:#8e8e93; text-transform:uppercase; letter-spacing:.04em; margin-bottom:.45rem; }
            #ugc-ios-sheet .ugc-ios-permission-copy .ugc-copy-permission-request { width:100% !important; min-height:42px; margin:.45rem 0 0 !important; border-radius:12px !important; }
            #ugc-ios-sheet .ugc-ios-permission-actions { display:grid !important; grid-template-columns:1fr 1fr; gap:.5rem !important; margin-top:.6rem !important; }
            #ugc-ios-sheet .ugc-ios-permission-actions form { width:100% !important; }
            #ugc-ios-sheet .ugc-ios-permission-actions form:first-child { grid-column:1 / -1; }
            #ugc-ios-sheet .ugc-ios-permission-actions button { width:100% !important; min-height:44px; border-radius:12px !important; font-size:.75rem !important; }
            #ugc-ios-sheet .ugc-ios-details-toggle { width:100%; min-height:48px; border:0; background:#fff; display:flex; align-items:center; justify-content:space-between; padding:0 .95rem; font-size:.82rem; color:#1c1c1e; font-weight:650; }
            #ugc-ios-sheet .ugc-ios-details-toggle span:last-child { color:#8e8e93; transition:transform 150ms ease; }
            #ugc-ios-sheet.ugc-ios-details-open .ugc-ios-details-toggle span:last-child { transform:rotate(90deg); }
            #ugc-ios-sheet .ugc-ios-details-body { display:none; border-top:1px solid #e5e5ea; padding:.75rem .95rem; }
            #ugc-ios-sheet.ugc-ios-details-open .ugc-ios-details-body { display:block; }
            #ugc-ios-sheet .ugc-ios-details-body > * { margin-top:.5rem !important; }
            #ugc-ios-sheet .ugc-ios-details-body > *:first-child { margin-top:0 !important; }
        }
    `;
    document.head.appendChild(style);

    function text(node) { return (node && node.textContent || '').trim(); }
    function directChildren(node) { return node ? Array.from(node.children || []) : []; }
    function hasText(node, value) { return text(node).toLowerCase().includes(value.toLowerCase()); }
    function cardBody(card) { return directChildren(card).find((node) => node.classList && node.classList.contains('p-3.5')); }

    function setupHeader() {
        const heading = Array.from(document.querySelectorAll('h1')).find((node) => text(node) === 'Community Content');
        if (heading) heading.textContent = 'Community';

        const controls = document.querySelector('.ugc-mobile-controls-wrap');
        if (!controls || document.getElementById('ugc-ios-toolbar')) return;

        const toolbar = document.createElement('div');
        toolbar.id = 'ugc-ios-toolbar';
        toolbar.className = 'ugc-ios-toolbar';
        toolbar.innerHTML = `
            <button type="button" data-action="search"><span>⌕</span> Search</button>
            <button type="button" data-action="filter"><span>☷</span> Filters</button>
            <button type="button" data-action="select"><span>✓</span> Select</button>
        `;
        controls.insertBefore(toolbar, controls.firstChild);

        const searchButton = toolbar.querySelector('[data-action="search"]');
        const filterButton = toolbar.querySelector('[data-action="filter"]');
        const selectButton = toolbar.querySelector('[data-action="select"]');
        const filterToggle = document.getElementById('ugc-mobile-filter-toggle');

        searchButton.addEventListener('click', () => {
            const open = !document.body.classList.contains('ugc-ios-search-open');
            document.body.classList.toggle('ugc-ios-search-open', open);
            searchButton.classList.toggle('active', open);
            if (open) window.setTimeout(() => document.getElementById('ugc-search')?.focus(), 30);
        });
        filterButton.addEventListener('click', () => {
            const panel = document.getElementById('ugc-mobile-filter-panel');
            if (!panel) return;
            const willOpen = panel.classList.contains('ugc-mobile-filter-panel-closed');
            panel.classList.toggle('ugc-mobile-filter-panel-closed', !willOpen);
            if (filterToggle) filterToggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            filterButton.classList.toggle('active', willOpen);
        });
        selectButton.addEventListener('click', () => {
            const open = !document.body.classList.contains('ugc-ios-select-open');
            document.body.classList.toggle('ugc-ios-select-open', open);
            selectButton.classList.toggle('active', open);
        });
    }

    let sheet = document.getElementById('ugc-ios-sheet');
    let backdrop = document.getElementById('ugc-ios-sheet-backdrop');
    if (!backdrop) {
        backdrop = document.createElement('div');
        backdrop.id = 'ugc-ios-sheet-backdrop';
        document.body.appendChild(backdrop);
    }
    if (!sheet) {
        sheet = document.createElement('section');
        sheet.id = 'ugc-ios-sheet';
        sheet.setAttribute('role', 'dialog');
        sheet.setAttribute('aria-modal', 'true');
        sheet.setAttribute('aria-label', 'Community content details');
        sheet.innerHTML = `
            <div class="ugc-ios-handle-wrap"><div class="ugc-ios-handle"></div></div>
            <div class="ugc-ios-sheet-scroll">
                <div class="ugc-ios-sheet-header">
                    <div class="ugc-ios-sheet-title"><h3>Community content</h3><p></p></div>
                    <button type="button" class="ugc-ios-close" aria-label="Close">×</button>
                </div>
                <div class="ugc-ios-media"></div>
                <div class="ugc-ios-section ugc-ios-content-section"><div class="ugc-ios-section-pad">
                    <div class="ugc-ios-caption"></div><div class="ugc-ios-sheet-metrics"></div>
                </div></div>
                <div class="ugc-ios-section ugc-ios-permission-section"><div class="ugc-ios-section-pad">
                    <div class="ugc-ios-section-title">Permission</div><div class="ugc-ios-permission-copy"></div>
                </div></div>
                <div class="ugc-ios-section ugc-ios-details-section">
                    <button type="button" class="ugc-ios-details-toggle"><span>Details</span><span>›</span></button>
                    <div class="ugc-ios-details-body"></div>
                </div>
                <div style="height:.85rem"></div>
            </div>
        `;
        document.body.appendChild(sheet);
    }

    const sheetTitle = sheet.querySelector('.ugc-ios-sheet-title h3');
    const sheetContributor = sheet.querySelector('.ugc-ios-sheet-title p');
    const sheetMedia = sheet.querySelector('.ugc-ios-media');
    const sheetCaption = sheet.querySelector('.ugc-ios-caption');
    const sheetMetrics = sheet.querySelector('.ugc-ios-sheet-metrics');
    const permissionCopy = sheet.querySelector('.ugc-ios-permission-copy');
    const detailsBody = sheet.querySelector('.ugc-ios-details-body');
    const closeButton = sheet.querySelector('.ugc-ios-close');
    const detailsToggle = sheet.querySelector('.ugc-ios-details-toggle');
    let currentCard = null;

    function cloneVideo(video) {
        const clone = document.createElement('video');
        clone.controls = true;
        clone.playsInline = true;
        clone.preload = 'metadata';
        if (video.poster) clone.poster = video.poster;
        const source = video.querySelector('source');
        if (source) {
            const clonedSource = document.createElement('source');
            clonedSource.src = source.src;
            if (source.type) clonedSource.type = source.type;
            clone.appendChild(clonedSource);
        } else if (video.currentSrc || video.src) {
            clone.src = video.currentSrc || video.src;
        }
        return clone;
    }

    function setSheetMedia(card) {
        sheetMedia.innerHTML = '';
        const video = card.querySelector('video');
        const img = card.querySelector('img');
        if (video) sheetMedia.appendChild(cloneVideo(video));
        else if (img) {
            const clone = document.createElement('img');
            clone.src = img.currentSrc || img.src;
            clone.alt = img.alt || '';
            sheetMedia.appendChild(clone);
        } else {
            sheetMedia.style.display = 'none';
            return;
        }
        sheetMedia.style.display = 'flex';
    }

    function permissionPanelFor(card) {
        const body = cardBody(card);
        return directChildren(body).find((node) => hasText(node, 'Creator permission') && node.querySelector('form[action*="permission"]')) || null;
    }

    function detailNodesFor(card) {
        const body = cardBody(card);
        if (!body) return [];
        return directChildren(body).filter((node) => {
            if (hasText(node, 'Attached to') && hasText(node, 'Contributor consent')) return true;
            if (hasText(node, 'Original source') && node.querySelector('a[target="_blank"]')) return true;
            if (hasText(node, 'Studio usage')) return true;
            return false;
        });
    }

    function cleanClonedPermission(panel) {
        const clone = panel.cloneNode(true);
        clone.className = '';
        const statusRow = clone.querySelector('.flex.items-start.justify-between');
        const actionRow = Array.from(clone.querySelectorAll('div')).find((node) => node.classList.contains('flex') && node.classList.contains('flex-wrap') && node.querySelector('form'));
        if (statusRow) {
            statusRow.className = '';
            const dot = statusRow.querySelector('.rounded-full');
            if (dot) dot.remove();
        }
        if (actionRow) actionRow.className = 'ugc-ios-permission-actions';
        const copy = clone.querySelector('.ugc-copy-permission-request');
        if (copy) {
            const wrap = document.createElement('div');
            wrap.className = 'ugc-ios-permission-copy-button';
            copy.parentNode.insertBefore(wrap, copy);
            wrap.appendChild(copy);
        }
        clone.querySelectorAll('button').forEach((button) => button.removeAttribute('disabled'));
        return clone;
    }

    function renderSheet(card) {
        currentCard = card;
        const body = cardBody(card);
        const heading = card.querySelector('h2');
        const contributor = heading ? heading.nextElementSibling : null;
        const caption = body ? directChildren(body).find((node) => node.tagName === 'P' && node !== contributor && node.classList.contains('line-clamp-3')) : null;
        const intelligence = card.querySelector('.ugc-discovery-intelligence');
        const permission = permissionPanelFor(card);

        sheetTitle.textContent = text(heading) || 'Community content';
        sheetContributor.textContent = text(contributor);
        setSheetMedia(card);
        sheetCaption.textContent = caption ? text(caption) : '';
        sheet.querySelector('.ugc-ios-content-section').style.display = (caption || intelligence) ? '' : 'none';
        sheetMetrics.innerHTML = intelligence ? intelligence.innerHTML : '';

        permissionCopy.innerHTML = '';
        if (permission) {
            permissionCopy.appendChild(cleanClonedPermission(permission));
            sheet.querySelector('.ugc-ios-permission-section').style.display = '';
        } else {
            sheet.querySelector('.ugc-ios-permission-section').style.display = 'none';
        }

        detailsBody.innerHTML = '';
        detailNodesFor(card).forEach((node) => detailsBody.appendChild(node.cloneNode(true)));
        sheet.querySelector('.ugc-ios-details-section').style.display = detailsBody.children.length ? '' : 'none';
        sheet.classList.remove('ugc-ios-details-open');
        sheet.querySelector('.ugc-ios-sheet-scroll').scrollTop = 0;
    }

    function openSheet(card) {
        renderSheet(card);
        backdrop.classList.add('open');
        sheet.classList.add('open');
        document.body.style.overflow = 'hidden';
    }

    function closeSheet() {
        sheet.querySelectorAll('video').forEach((video) => video.pause());
        backdrop.classList.remove('open');
        sheet.classList.remove('open', 'ugc-ios-details-open');
        document.body.style.overflow = '';
        currentCard = null;
    }

    closeButton.addEventListener('click', closeSheet);
    backdrop.addEventListener('click', closeSheet);
    detailsToggle.addEventListener('click', () => sheet.classList.toggle('ugc-ios-details-open'));
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && sheet.classList.contains('open')) closeSheet(); });

    /* Drag sheet down to dismiss. */
    let touchStartY = 0;
    let touchDelta = 0;
    const handleWrap = sheet.querySelector('.ugc-ios-handle-wrap');
    handleWrap.addEventListener('touchstart', (event) => {
        touchStartY = event.touches[0].clientY;
        touchDelta = 0;
        sheet.style.transition = 'none';
    }, { passive:true });
    handleWrap.addEventListener('touchmove', (event) => {
        touchDelta = Math.max(0, event.touches[0].clientY - touchStartY);
        sheet.style.transform = `translateY(${touchDelta}px)`;
    }, { passive:true });
    handleWrap.addEventListener('touchend', () => {
        sheet.style.transition = '';
        sheet.style.transform = '';
        if (touchDelta > 90) closeSheet();
    });

    function enhanceCard(card) {
        if (card.dataset.iosFeedReady === '1') return;
        card.dataset.iosFeedReady = '1';
        const body = cardBody(card);
        if (!body) return;

        const children = directChildren(body);
        const header = children.find((node) => node.classList.contains('flex') && node.classList.contains('items-start'));
        const badges = header ? header.querySelector('.flex.flex-wrap.items-center') : null;
        if (badges) badges.classList.add('ugc-ios-feed-badges');

        children.forEach((node) => {
            if (node === header) return;
            if (node.tagName === 'P' && node.classList.contains('line-clamp-3')) return;
            if (node.classList.contains('ugc-discovery-intelligence')) return;
            if (hasText(node, 'Attached to') && hasText(node, 'Contributor consent')) node.classList.add('ugc-ios-feed-hidden');
            else if (hasText(node, 'Original source')) node.classList.add('ugc-ios-feed-hidden');
            else if (hasText(node, 'Creator permission')) node.classList.add('ugc-ios-feed-hidden');
            else if (hasText(node, 'Awaiting permission')) node.classList.add('ugc-ios-feed-hidden');
            else if (hasText(node, 'Studio usage')) node.classList.add('ugc-ios-feed-hidden');
        });

        let status = body.querySelector('.ugc-ios-card-status');
        if (!status) {
            status = document.createElement('div');
            status.className = 'ugc-ios-card-status';
            status.innerHTML = '<span class="ugc-ios-status-pill method">Discovered</span><span class="ugc-ios-status-pill relevant">✓ Relevant</span><span class="ugc-ios-status-pill permission">Permission ›</span>';
            body.appendChild(status);
        }

        card.addEventListener('click', (event) => {
            if (event.target.closest('a,button,input,select,textarea,form,label,video')) return;
            openSheet(card);
        });
    }

    function syncDynamicData() {
        grid.querySelectorAll('.ugc-card').forEach((card) => {
            enhanceCard(card);
            const status = card.querySelector('.ugc-ios-card-status');
            if (!status) return;
            const intelligence = card.querySelector('.ugc-discovery-intelligence');
            const methodBadge = intelligence ? Array.from(intelligence.querySelectorAll('span')).find((node) => /keyword|hashtag|location|account/i.test(text(node))) : null;
            if (methodBadge) status.querySelector('.method').textContent = text(methodBadge).split('·')[0].trim();
            const relevance = card.dataset.relevance || '';
            const relevancePill = status.querySelector('.relevant');
            if (relevance === 'low') { relevancePill.textContent = '⚠ Low relevance'; relevancePill.classList.remove('relevant'); }
            else if (relevance === 'strong') relevancePill.textContent = '✓ Strong match';
            const permissionPanel = permissionPanelFor(card);
            if (permissionPanel) {
                const permissionText = text(permissionPanel).toLowerCase();
                const pill = status.querySelector('.permission');
                if (permissionText.includes('permission granted')) pill.textContent = 'Granted ›';
                else if (permissionText.includes('permission declined')) pill.textContent = 'Declined ›';
                else if (permissionText.includes('permission requested')) pill.textContent = 'Requested ›';
                else pill.textContent = 'Permission ›';
            }
        });
    }

    setupHeader();
    syncDynamicData();
    const observer = new MutationObserver(() => syncDynamicData());
    observer.observe(grid, { childList:true, subtree:true });
})();