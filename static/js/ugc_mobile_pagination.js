(function () {
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    if (!document.body.classList.contains('ugc-mobile-community')) return;

    const grid = document.getElementById('ugc-card-grid');
    if (!grid || document.getElementById('ugc-mobile-server-pagination')) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    /* The optimized server view returns 16 cards on mobile. If we somehow
       receive a larger legacy response, let the virtual-feed fallback handle it. */
    if (!cards.length || cards.length > 20) return;

    const params = new URLSearchParams(window.location.search);
    const tab = params.get('tab') || 'pending';
    const page = Math.max(1, Number.parseInt(params.get('page') || '1', 10) || 1);
    const PAGE_SIZE = 16;

    const tabLink = Array.from(document.querySelectorAll('a[href*="?tab="]')).find((link) => {
        try {
            const url = new URL(link.href, window.location.href);
            return (url.searchParams.get('tab') || 'pending') === tab;
        } catch (error) {
            return false;
        }
    });
    const countNode = tabLink ? tabLink.querySelector('span') : null;
    const total = countNode ? Number.parseInt((countNode.textContent || '').replace(/[^0-9]/g, ''), 10) || 0 : 0;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (totalPages <= 1) return;

    function pageHref(targetPage) {
        const next = new URL(window.location.href);
        if (targetPage <= 1) next.searchParams.delete('page');
        else next.searchParams.set('page', String(targetPage));
        return next.pathname + (next.searchParams.toString() ? `?${next.searchParams.toString()}` : '');
    }

    const nav = document.createElement('nav');
    nav.id = 'ugc-mobile-server-pagination';
    nav.setAttribute('aria-label', 'Community content pages');
    nav.style.cssText = 'display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:10px;padding:10px 0 calc(22px + env(safe-area-inset-bottom));font-family:system-ui,-apple-system,BlinkMacSystemFont,sans-serif;';

    const previous = page > 1
        ? `<a href="${pageHref(page - 1)}" style="justify-self:start;text-decoration:none;background:#fff;border:1px solid #e5e5ea;border-radius:12px;padding:11px 15px;color:#6d28d9;font-size:14px;font-weight:650;">‹ Previous</a>`
        : '<span></span>';
    const next = page < totalPages
        ? `<a href="${pageHref(page + 1)}" style="justify-self:end;text-decoration:none;background:#7c3aed;border-radius:12px;padding:11px 15px;color:#fff;font-size:14px;font-weight:650;">Next ›</a>`
        : '<span></span>';

    nav.innerHTML = `${previous}<div style="text-align:center;color:#8e8e93;font-size:12px;line-height:1.35;"><strong style="display:block;color:#3a3a3c;font-size:13px;">Page ${page} of ${totalPages}</strong>${total.toLocaleString()} items</div>${next}`;
    grid.insertAdjacentElement('afterend', nav);
})();
