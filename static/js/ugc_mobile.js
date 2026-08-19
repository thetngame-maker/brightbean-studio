(function () {
    function text(node) { return (node && node.textContent || '').trim(); }
    function isMobile() { return window.matchMedia('(max-width: 767px)').matches; }

    function loadRelevanceFilter() {
        if (document.querySelector('script[data-ugc-relevance]')) return;
        const script = document.createElement('script');
        script.src = '/static/js/ugc_discovered_relevance.js';
        script.dataset.ugcRelevance = '1';
        const current = document.currentScript;
        if (current && current.nonce) script.nonce = current.nonce;
        document.head.appendChild(script);
    }

    function loadMobileCardCleanup() {
        if (!document.body.classList.contains('ugc-mobile-community')) return;
        if (document.querySelector('script[data-ugc-mobile-cards]')) return;
        const script = document.createElement('script');
        script.src = '/static/js/ugc_mobile_cards.js';
        script.dataset.ugcMobileCards = '1';
        const current = document.currentScript;
        if (current && current.nonce) script.nonce = current.nonce;
        document.head.appendChild(script);
    }

    function loadMobileFilterSheet() {
        if (!document.body.classList.contains('ugc-mobile-community') || !isMobile()) return;
        if (document.querySelector('script[data-ugc-mobile-filter-sheet]')) return;
        const script = document.createElement('script');
        script.src = '/static/js/ugc_mobile_filter_sheet.js';
        script.dataset.ugcMobileFilterSheet = '1';
        const current = document.currentScript;
        if (current && current.nonce) script.nonce = current.nonce;
        document.head.appendChild(script);
    }

    function loadMobilePagination() {
        if (!document.body.classList.contains('ugc-mobile-community') || !isMobile()) return;
        if (document.querySelector('script[data-ugc-mobile-pagination]')) return;
        const script = document.createElement('script');
        script.src = '/static/js/ugc_mobile_pagination.js';
        script.dataset.ugcMobilePagination = '1';
        const current = document.currentScript;
        if (current && current.nonce) script.nonce = current.nonce;
        document.head.appendChild(script);
    }

    function setupMobileBulkToolbar() {
        if (!document.body.classList.contains('ugc-mobile-community')) return;

        function enhance(toolbar) {
            if (!toolbar || toolbar.dataset.mobileBulkReady === '1') return;
            toolbar.dataset.mobileBulkReady = '1';
            toolbar.classList.add('ugc-mobile-bulk-toolbar');

            const actionButtons = Array.from(toolbar.querySelectorAll('.ugc-bulk-action'));
            const actionRow = actionButtons.length ? actionButtons[0].parentElement : null;
            if (actionRow) actionRow.classList.add('ugc-mobile-bulk-actions');

            const selectionRow = toolbar.querySelector('.flex.items-center.gap-3.min-w-0');
            if (selectionRow) selectionRow.classList.add('ugc-mobile-bulk-selection-row');

            function selectedCount() {
                return document.querySelectorAll('.ugc-select-item:checked').length;
            }

            function syncExpandedState() {
                const count = selectedCount();
                toolbar.classList.toggle('ugc-mobile-bulk-has-selection', count > 0);
                if (actionRow) actionRow.setAttribute('aria-hidden', count > 0 ? 'false' : 'true');
            }

            toolbar.addEventListener('change', (event) => {
                if (event.target && (event.target.matches('.ugc-select-item') || event.target.matches('#ugc-select-visible'))) {
                    window.setTimeout(syncExpandedState, 0);
                }
            });

            document.addEventListener('change', (event) => {
                if (event.target && event.target.matches('.ugc-select-item')) window.setTimeout(syncExpandedState, 0);
            });

            toolbar.addEventListener('click', (event) => {
                if (event.target && event.target.closest('#ugc-select-top-ten')) {
                    window.setTimeout(syncExpandedState, 25);
                }
            });

            const observer = new MutationObserver(() => {
                const topTen = toolbar.querySelector('#ugc-select-top-ten');
                if (topTen) topTen.classList.add('ugc-mobile-top-ten');
                syncExpandedState();
            });
            observer.observe(toolbar, { childList: true, subtree: true });

            const topTen = toolbar.querySelector('#ugc-select-top-ten');
            if (topTen) topTen.classList.add('ugc-mobile-top-ten');
            syncExpandedState();
        }

        const existing = document.getElementById('ugc-bulk-permission-form');
        if (existing) enhance(existing);

        const pageObserver = new MutationObserver(() => {
            const toolbar = document.getElementById('ugc-bulk-permission-form');
            if (toolbar) enhance(toolbar);
        });
        pageObserver.observe(document.body, { childList: true, subtree: true });
    }

    function setupCommunityContent() {
        const heading = Array.from(document.querySelectorAll('h1')).find((node) => text(node) === 'Community Content');
        if (!heading) return false;
        document.body.classList.add('ugc-mobile-community');
        loadRelevanceFilter();

        const headerRoot = heading.closest('.flex.flex-col.gap-4');
        if (headerRoot) headerRoot.classList.add('ugc-mobile-page-header');

        const headerTop = heading.closest('.flex.flex-col.sm\\:flex-row');
        if (headerTop) {
            const actionArea = Array.from(headerTop.children).find((node) => node !== headerTop.firstElementChild && node.classList.contains('flex'));
            if (actionArea) actionArea.classList.add('ugc-mobile-header-actions');
        }

        const tabLink = document.querySelector('a[href*="?tab=discovered"]');
        if (tabLink && tabLink.parentElement) tabLink.parentElement.classList.add('ugc-mobile-tabs');

        const searchForm = document.getElementById('ugc-search-form');
        const sort = document.getElementById('ugc-sort');
        const controlsWrap = searchForm ? searchForm.parentElement : null;
        const filterPanel = sort ? sort.parentElement : null;
        if (controlsWrap) controlsWrap.classList.add('ugc-mobile-controls-wrap');
        if (!controlsWrap || !filterPanel) {
            setupMobileBulkToolbar();
            loadMobileCardCleanup();
            window.setTimeout(loadMobilePagination, 100);
            return true;
        }
        filterPanel.classList.add('ugc-mobile-filter-panel');

        let toggle = document.getElementById('ugc-mobile-filter-toggle');
        if (!toggle) {
            toggle = document.createElement('button');
            toggle.id = 'ugc-mobile-filter-toggle';
            toggle.type = 'button';
            toggle.className = 'ugc-mobile-filter-toggle';
            toggle.setAttribute('aria-controls', 'ugc-mobile-filter-panel');
            toggle.innerHTML = '<span>Filters & sort</span><span class="ugc-filter-summary">All content</span><span class="ugc-filter-chevron">⌄</span>';
            filterPanel.id = 'ugc-mobile-filter-panel';
            controlsWrap.insertBefore(toggle, filterPanel);
        }

        function selects() {
            return Array.from(filterPanel.querySelectorAll('select'));
        }
        function activeLabels() {
            return selects().filter((select) => Boolean(select.value)).map((select) => {
                const option = select.options && select.options[select.selectedIndex];
                return option ? text(option) : '';
            }).filter(Boolean);
        }
        function updateSummary() {
            const labels = activeLabels();
            const summary = toggle.querySelector('.ugc-filter-summary');
            if (summary) summary.textContent = labels.length ? labels.join(' · ') : 'All content';
        }
        function setOpen(open) {
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            filterPanel.classList.toggle('ugc-mobile-filter-panel-closed', !open);
        }

        if (isMobile()) setOpen(activeLabels().length > 0);
        toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
        filterPanel.addEventListener('change', updateSummary);

        const observer = new MutationObserver(() => {
            updateSummary();
            if (isMobile() && !filterPanel.classList.contains('ugc-mobile-filter-panel-closed') && toggle.getAttribute('aria-expanded') !== 'true') {
                setOpen(false);
            }
        });
        observer.observe(filterPanel, { childList: true, subtree: true });
        updateSummary();

        window.addEventListener('resize', () => {
            if (!isMobile()) filterPanel.classList.remove('ugc-mobile-filter-panel-closed');
            else if (toggle.getAttribute('aria-expanded') !== 'true') filterPanel.classList.add('ugc-mobile-filter-panel-closed');
        });

        setupMobileBulkToolbar();
        loadMobileCardCleanup();
        window.setTimeout(loadMobileFilterSheet, 60);
        window.setTimeout(loadMobilePagination, 120);
        return true;
    }

    function setupDiscoverySearches() {
        const heading = Array.from(document.querySelectorAll('h1')).find((node) => text(node) === 'Discovery Searches');
        if (!heading) return false;
        document.body.classList.add('ugc-mobile-discovery-searches');

        const headerRoot = heading.closest('.flex.flex-col.sm\\:flex-row');
        if (headerRoot) {
            headerRoot.classList.add('ugc-mobile-page-header');
            const providerBadge = Array.from(headerRoot.children).find((node) => node.tagName === 'SPAN');
            if (providerBadge) providerBadge.classList.add('ugc-mobile-provider-badge');
        }

        const contentGrid = Array.from(document.querySelectorAll('div.grid')).find((node) => {
            return node.querySelector('form[action*="discovery"]') && Array.from(node.querySelectorAll('h2')).some((h) => text(h) === 'Saved searches');
        });
        if (!contentGrid) return true;
        contentGrid.classList.add('ugc-mobile-discovery-grid');

        const addSection = contentGrid.querySelector(':scope > section:first-child');
        const savedSection = Array.from(contentGrid.querySelectorAll(':scope > section')).find((section) => Array.from(section.querySelectorAll('h2')).some((h) => text(h) === 'Saved searches'));

        if (savedSection) {
            const savedHeader = Array.from(savedSection.children).find((node) => node.querySelector && Array.from(node.querySelectorAll('h2')).some((h) => text(h) === 'Saved searches'));
            if (savedHeader) savedHeader.classList.add('ugc-mobile-saved-searches-header');

            savedSection.querySelectorAll('article').forEach((card) => {
                card.classList.add('ugc-mobile-saved-search-card');
                const layout = card.firstElementChild;
                if (layout) {
                    layout.classList.add('ugc-mobile-search-card-layout');
                    const actionArea = Array.from(layout.children).find((node) => {
                        const buttons = node.querySelectorAll ? node.querySelectorAll('button, a, select').length : 0;
                        return buttons >= 3;
                    });
                    if (actionArea) actionArea.classList.add('ugc-mobile-search-actions');
                }
            });
        }

        if (addSection) {
            addSection.classList.add('ugc-mobile-add-search-section');
            let addToggle = document.getElementById('ugc-mobile-add-search-toggle');
            if (!addToggle) {
                addToggle = document.createElement('button');
                addToggle.id = 'ugc-mobile-add-search-toggle';
                addToggle.type = 'button';
                addToggle.className = 'ugc-mobile-add-search-toggle';
                addToggle.innerHTML = '<span>＋ Add discovery search</span><span class="ugc-filter-chevron">⌄</span>';
                contentGrid.insertBefore(addToggle, addSection);
            }
            function setAddOpen(open) {
                addToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
                addSection.classList.toggle('ugc-mobile-collapsed', !open && isMobile());
            }
            setAddOpen(!isMobile());
            addToggle.addEventListener('click', () => setAddOpen(addToggle.getAttribute('aria-expanded') !== 'true'));
            window.addEventListener('resize', () => {
                if (!isMobile()) addSection.classList.remove('ugc-mobile-collapsed');
                else if (addToggle.getAttribute('aria-expanded') !== 'true') addSection.classList.add('ugc-mobile-collapsed');
            });
        }
        return true;
    }

    function init() {
        setupCommunityContent();
        setupDiscoverySearches();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})();