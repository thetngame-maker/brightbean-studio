(function () {
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    const grid = document.getElementById('ugc-card-grid');
    if (!grid) return;

    const style = document.createElement('style');
    style.textContent = `
        @media (max-width: 767px) {
            body.ugc-mobile-community .ugc-card .ugc-mobile-detail-section { display:none !important; }
            body.ugc-mobile-community .ugc-card.ugc-mobile-details-open .ugc-mobile-detail-section { display:flex !important; }
            body.ugc-mobile-community .ugc-card .ugc-mobile-detail-grid.ugc-mobile-detail-section { display:none !important; }
            body.ugc-mobile-community .ugc-card.ugc-mobile-details-open .ugc-mobile-detail-grid.ugc-mobile-detail-section { display:grid !important; }
            body.ugc-mobile-community .ugc-mobile-card-details-toggle {
                width:100%; min-height:40px; margin-top:.65rem; border:1px solid #e7e5e4; border-radius:.7rem;
                background:#fafaf9; color:#57534e; display:flex; align-items:center; justify-content:space-between;
                padding:0 .75rem; font-size:.72rem; font-weight:700;
            }
            body.ugc-mobile-community .ugc-mobile-card-details-toggle .chev { color:#8b5cf6; transition:transform 150ms ease; }
            body.ugc-mobile-community .ugc-card.ugc-mobile-details-open .ugc-mobile-card-details-toggle .chev { transform:rotate(180deg); }
            body.ugc-mobile-community .ugc-mobile-details-wrap { margin-top:.55rem; display:flex; flex-direction:column; gap:.5rem; }
            body.ugc-mobile-community .ugc-mobile-details-wrap > * { margin-top:0 !important; }
            body.ugc-mobile-community .ugc-mobile-detail-grid { gap:.5rem !important; }
            body.ugc-mobile-community .ugc-mobile-detail-grid > div { padding:.65rem !important; }
            body.ugc-mobile-community .ugc-mobile-source-block { padding:.65rem !important; }
            body.ugc-mobile-community .ugc-mobile-source-block .text-xs { font-size:.72rem !important; }
            body.ugc-mobile-community .ugc-mobile-source-block .text-\[10px\] { font-size:.62rem !important; }
            body.ugc-mobile-community .ugc-mobile-permission-panel { margin-top:.65rem !important; padding:.7rem !important; }
            body.ugc-mobile-community .ugc-mobile-permission-panel .flex.flex-wrap.gap-1\.5 { display:grid !important; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.4rem !important; }
            body.ugc-mobile-community .ugc-mobile-permission-panel form,
            body.ugc-mobile-community .ugc-mobile-permission-panel form button { width:100% !important; }
            body.ugc-mobile-community .ugc-mobile-permission-panel form button { min-height:40px; padding:.35rem .25rem !important; font-size:.68rem !important; line-height:1.15; }
            body.ugc-mobile-community .ugc-mobile-permission-panel .ugc-copy-permission-request { min-height:34px; }
            body.ugc-mobile-community .ugc-mobile-awaiting-footer { display:none !important; }
            body.ugc-mobile-community .ugc-card p.line-clamp-3 { -webkit-line-clamp:2 !important; }
            body.ugc-mobile-community #ugc-card-grid .ugc-card > .p-3\.5 { padding:.72rem !important; }
            body.ugc-mobile-community .ugc-card .ugc-discovery-intelligence { margin-top:.45rem !important; }
            body.ugc-mobile-community .ugc-card h2 { font-size:.95rem !important; }
        }
    `;
    document.head.appendChild(style);

    function directChildren(node) { return node ? Array.from(node.children || []) : []; }
    function hasText(node, value) { return (node && node.textContent || '').toLowerCase().includes(value.toLowerCase()); }

    grid.querySelectorAll('.ugc-card').forEach((card) => {
        if (card.dataset.mobileCardClean === '1') return;
        card.dataset.mobileCardClean = '1';
        const body = directChildren(card).find((node) => node.classList && node.classList.contains('p-3.5'));
        if (!body) return;

        const children = directChildren(body);
        const detailGrid = children.find((node) => node.classList.contains('grid') && hasText(node, 'Attached to') && hasText(node, 'Contributor consent'));
        const sourceBlock = children.find((node) => hasText(node, 'Original source') && node.querySelector('a[target="_blank"]'));
        const permissionPanel = children.find((node) => hasText(node, 'Creator permission') && node.querySelector('form[action*="permission"]'));
        const footer = children.find((node) => hasText(node, 'Awaiting permission') && node.classList.contains('border-t'));

        if (permissionPanel) permissionPanel.classList.add('ugc-mobile-permission-panel');
        if (footer) footer.classList.add('ugc-mobile-awaiting-footer');

        const detailNodes = [detailGrid, sourceBlock].filter(Boolean);
        if (!detailNodes.length) return;

        if (detailGrid) detailGrid.classList.add('ugc-mobile-detail-section', 'ugc-mobile-detail-grid');
        if (sourceBlock) sourceBlock.classList.add('ugc-mobile-detail-section', 'ugc-mobile-source-block');

        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'ugc-mobile-card-details-toggle';
        toggle.setAttribute('aria-expanded', 'false');
        toggle.innerHTML = '<span>Details</span><span class="chev">⌄</span>';

        const detailsWrap = document.createElement('div');
        detailsWrap.className = 'ugc-mobile-details-wrap';
        detailNodes.forEach((node) => detailsWrap.appendChild(node));

        const insertionPoint = permissionPanel || footer || body.lastElementChild;
        if (insertionPoint) body.insertBefore(toggle, insertionPoint);
        else body.appendChild(toggle);
        body.insertBefore(detailsWrap, insertionPoint || null);

        toggle.addEventListener('click', () => {
            const open = !card.classList.contains('ugc-mobile-details-open');
            card.classList.toggle('ugc-mobile-details-open', open);
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
    });
})();