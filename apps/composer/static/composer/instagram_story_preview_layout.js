/* TN Social Studio — compact Instagram Story preview layout.
 *
 * Keeps the Story preview inside BrightBean's existing right-side Preview
 * panel instead of letting it consume vertical space in the main composer.
 */
(function () {
    'use strict';

    const PREVIEW_ID = 'tn-instagram-story-preview';
    const STYLE_ID = 'tn-instagram-story-preview-layout-style';

    function ensureStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = `
            .panel-right #${PREVIEW_ID}{
                padding:12px 14px 16px !important;
                margin:0 !important;
                border-top:1px solid #e7e5e4;
                background:#fafaf9;
                display:flex !important;
                flex-direction:column;
                align-items:center;
                gap:8px !important;
                flex:0 0 auto;
            }
            .panel-right #${PREVIEW_ID} .tn-story-label{
                width:100%;
                font-size:10px !important;
                letter-spacing:.08em;
                text-align:left;
            }
            .panel-right #${PREVIEW_ID} .tn-story-frame{
                width:min(184px,72%) !important;
                max-height:328px;
                border-radius:16px !important;
                box-shadow:0 5px 18px rgba(0,0,0,.10) !important;
            }
            .panel-right #${PREVIEW_ID} .tn-story-top{
                padding:9px !important;
                font-size:10px !important;
            }
            .panel-right #${PREVIEW_ID} .tn-story-note{
                max-width:230px !important;
                font-size:10px !important;
                line-height:1.35;
            }
            @media (max-width:1023px){
                .panel-right #${PREVIEW_ID} .tn-story-frame{
                    width:min(210px,66%) !important;
                }
            }
        `;
        document.head.appendChild(style);
    }

    function sidePanelBody() {
        const panel = document.querySelector('.panel-right');
        if (!panel) return null;

        // Prefer the panel's scrollable preview-content region so the Story
        // preview scrolls with the normal platform preview cards.
        const scrollRegion = panel.querySelector('.panel-scroll');
        if (scrollRegion) return scrollRegion;

        // Fallback: use the panel itself. The header remains first because the
        // Story preview is appended after existing children.
        return panel;
    }

    function movePreview() {
        ensureStyles();
        const preview = document.getElementById(PREVIEW_ID);
        if (!preview) return;
        const target = sidePanelBody();
        if (!target || target.contains(preview)) return;
        target.appendChild(preview);
    }

    function boot() {
        movePreview();
        const observer = new MutationObserver(movePreview);
        observer.observe(document.body, { childList: true, subtree: true });
        document.body.addEventListener('previewUpdate', movePreview);
        document.body.addEventListener('htmx:afterSwap', function () {
            window.setTimeout(movePreview, 0);
        });
        window.setInterval(movePreview, 500);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot, { once: true });
    } else {
        boot();
    }
})();