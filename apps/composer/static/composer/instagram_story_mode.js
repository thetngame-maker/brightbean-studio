/* TN Social Studio — Instagram Story composer controls.
 *
 * Adds an explicit Feed/Reel vs Story selector for Instagram Login accounts,
 * plus an "Also add to Story" option for a single photo/video.  The server
 * persists these controls into PlatformPost.platform_extra.
 */
(function () {
    'use strict';

    const ROOT_SELECTOR = '.composer-main';
    const CONTAINER_ID = 'tn-instagram-story-controls';
    const stateByAccount = new Map();
    let lastSignature = '';

    function alpineData(root) {
        try {
            if (window.Alpine && typeof window.Alpine.$data === 'function') {
                return window.Alpine.$data(root);
            }
        } catch (_) {}
        return null;
    }

    function mediaSummary(app) {
        const items = Array.isArray(app.mediaItems) ? app.mediaItems : [];
        const count = items.length;
        const first = items[0] || null;
        return {
            items,
            count,
            isVideo: !!(first && first.is_video),
            isImage: !!(first && !first.is_video),
        };
    }

    function defaultFormat(media) {
        if (media.count > 1) return 'carousel';
        if (media.count === 1 && media.isVideo) return 'reel';
        if (media.count === 1) return 'image';
        return 'image';
    }

    function accountState(app, accountId, media) {
        const saved = (app.platformExtras && app.platformExtras[accountId]) || {};
        let state = stateByAccount.get(accountId);
        if (!state) {
            let format = String(saved.post_type || '').toLowerCase();
            if (!['image', 'reel', 'story', 'carousel', 'video'].includes(format)) {
                format = defaultFormat(media);
            }
            if (format === 'video') format = 'reel';
            state = {
                format,
                alsoStory: !!saved.also_story && format !== 'story',
                userChoseFormat: !!saved.post_type,
            };
            stateByAccount.set(accountId, state);
        } else if (!state.userChoseFormat && state.format !== 'story') {
            // Keep the automatic default in sync as media changes. A video
            // becomes Reel; a photo becomes Post; multiple items become Carousel.
            state.format = defaultFormat(media);
        }
        if (state.format === 'story') state.alsoStory = false;
        return state;
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function instagramAccounts(app) {
        const selected = Array.isArray(app.selectedAccounts) ? app.selectedAccounts : [];
        return selected
            .map(id => ({ id, meta: app.charLimits ? app.charLimits[id] : null }))
            .filter(entry => entry.meta && ['instagram_login', 'instagram'].includes(entry.meta.platform));
    }

    function findInsertAnchor(root) {
        const captionWrap = root.querySelector('.caption-wrap');
        if (!captionWrap) return null;
        // Caption wrapper sits inside the direct composer section that also owns
        // the label/template picker. Insert after that whole section.
        return captionWrap.parentElement || captionWrap;
    }

    function buttonClass(active) {
        return active
            ? 'border-orange-500 bg-orange-50 text-orange-700 ring-2 ring-orange-100'
            : 'border-stone-200 bg-white text-stone-600 hover:border-stone-300 hover:bg-stone-50';
    }

    function renderAccountCard(entry, app, media) {
        const id = entry.id;
        const meta = entry.meta || {};
        const name = escapeHtml(meta.name || 'Instagram');
        const state = accountState(app, id, media);
        const singleMedia = media.count === 1;
        const noMedia = media.count === 0;
        const multiple = media.count > 1;
        const primaryLabel = media.isVideo ? 'Reel' : 'Post';
        const primaryValue = media.isVideo ? 'reel' : (multiple ? 'carousel' : 'image');
        const primaryDisabled = noMedia;
        const storyDisabled = noMedia || multiple;
        const alsoDisabled = noMedia || multiple || state.format === 'story';

        let mediaHint = 'Add one photo or video to choose how Instagram should publish it.';
        if (multiple) mediaHint = 'Multiple files publish as a carousel. Story mode is available for a single photo or video.';
        else if (media.isVideo) mediaHint = 'Videos default to Reels. Choose Story to publish the video only as a Story.';
        else if (singleMedia) mediaHint = 'Photos default to a feed post. Choose Story to publish the photo only as a Story.';

        return `
            <section class="tn-ig-story-card border border-stone-200 rounded-xl bg-white p-4 space-y-3" data-account-id="${id}">
                <input type="hidden" name="instagram_post_type_${id}" value="${escapeHtml(state.format)}" data-role="format-input">
                <input type="hidden" name="instagram_also_story_${id}" value="${state.alsoStory ? 'true' : 'false'}" data-role="also-input">

                <div class="flex items-center gap-2">
                    <span class="inline-flex items-center justify-center w-5 h-5 rounded-[5px] text-white text-[10px] font-bold"
                          style="background:linear-gradient(135deg,#F58529,#DD2A7B,#8134AF)">◎</span>
                    <div class="min-w-0">
                        <div class="text-xs font-semibold text-stone-700">${name} · Instagram publishing</div>
                        <div class="text-[11px] text-stone-400">Choose where this media should appear.</div>
                    </div>
                </div>

                <div>
                    <label class="block text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-1.5">Publish as</label>
                    <div class="grid grid-cols-2 gap-2">
                        <button type="button" data-action="primary" ${primaryDisabled ? 'disabled' : ''}
                                class="tn-ig-format-btn px-3 py-2 rounded-lg border text-sm font-semibold transition-all ${buttonClass(state.format !== 'story')} ${primaryDisabled ? 'opacity-45 cursor-not-allowed' : 'cursor-pointer'}">
                            ${multiple ? 'Carousel' : primaryLabel}
                            <span class="block text-[10px] font-normal mt-0.5 opacity-70">${multiple ? 'Feed carousel' : (media.isVideo ? 'Instagram Reel' : 'Feed post')}</span>
                        </button>
                        <button type="button" data-action="story" ${storyDisabled ? 'disabled' : ''}
                                class="tn-ig-format-btn px-3 py-2 rounded-lg border text-sm font-semibold transition-all ${buttonClass(state.format === 'story')} ${storyDisabled ? 'opacity-45 cursor-not-allowed' : 'cursor-pointer'}">
                            Story
                            <span class="block text-[10px] font-normal mt-0.5 opacity-70">Story only</span>
                        </button>
                    </div>
                    <p class="text-[11px] text-stone-400 mt-1.5">${escapeHtml(mediaHint)}</p>
                </div>

                <div class="border-t border-stone-100 pt-3">
                    <label class="flex items-start justify-between gap-3 ${alsoDisabled ? 'opacity-50' : 'cursor-pointer'}">
                        <span class="text-sm text-stone-700">
                            Also add to Story
                            <span class="block text-[11px] text-stone-400 font-normal mt-0.5">Publish a second Story using the same photo or video.</span>
                        </span>
                        <span class="relative inline-flex w-9 h-5 flex-shrink-0">
                            <input type="checkbox" data-action="also-story" class="sr-only peer"
                                   ${state.alsoStory ? 'checked' : ''} ${alsoDisabled ? 'disabled' : ''}>
                            <span class="absolute inset-0 rounded-full bg-stone-200 peer-checked:bg-orange-500 transition-colors"></span>
                            <span class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform peer-checked:translate-x-4"></span>
                        </span>
                    </label>
                    ${state.format === 'story' ? '<p class="text-[11px] text-stone-400 mt-1">This is already a Story, so a second Story copy is not needed.</p>' : ''}
                    ${multiple ? '<p class="text-[11px] text-amber-600 mt-1">“Also add to Story” currently supports one photo or one video at a time.</p>' : ''}
                </div>

                ${state.format === 'story' ? '<div class="text-[11px] text-stone-500 bg-stone-50 rounded-lg px-3 py-2">Story publishing uses the media itself. The caption field is kept for your draft/history but is not placed onto the Story.</div>' : ''}
            </section>`;
    }

    function bindCard(card, app, media) {
        const accountId = card.dataset.accountId;
        const state = stateByAccount.get(accountId);
        if (!state) return;

        const primary = card.querySelector('[data-action="primary"]');
        const story = card.querySelector('[data-action="story"]');
        const also = card.querySelector('[data-action="also-story"]');

        if (primary) primary.addEventListener('click', function () {
            if (this.disabled) return;
            state.userChoseFormat = true;
            state.format = media.count > 1 ? 'carousel' : (media.isVideo ? 'reel' : 'image');
            render();
        });
        if (story) story.addEventListener('click', function () {
            if (this.disabled) return;
            state.userChoseFormat = true;
            state.format = 'story';
            state.alsoStory = false;
            render();
        });
        if (also) also.addEventListener('change', function () {
            state.alsoStory = !!this.checked;
            const hidden = card.querySelector('[data-role="also-input"]');
            if (hidden) hidden.value = state.alsoStory ? 'true' : 'false';
        });
    }

    function render() {
        const root = document.querySelector(ROOT_SELECTOR);
        if (!root) return;
        const app = alpineData(root);
        if (!app) return;

        const accounts = instagramAccounts(app);
        const media = mediaSummary(app);
        const anchor = findInsertAnchor(root);
        if (!anchor) return;

        let container = document.getElementById(CONTAINER_ID);
        if (!accounts.length) {
            if (container) container.remove();
            return;
        }
        if (!container) {
            container = document.createElement('div');
            container.id = CONTAINER_ID;
            container.className = 'space-y-3';
            anchor.insertAdjacentElement('afterend', container);
        }

        container.innerHTML = accounts.map(entry => renderAccountCard(entry, app, media)).join('');
        container.querySelectorAll('.tn-ig-story-card').forEach(card => bindCard(card, app, media));
    }

    function signature() {
        const root = document.querySelector(ROOT_SELECTOR);
        const app = root ? alpineData(root) : null;
        if (!app) return '';
        const selected = Array.isArray(app.selectedAccounts) ? app.selectedAccounts.join(',') : '';
        const media = Array.isArray(app.mediaItems)
            ? app.mediaItems.map(item => `${item.id || item.asset_id || ''}:${item.is_video ? 'v' : 'i'}`).join(',')
            : '';
        return selected + '|' + media;
    }

    function tick() {
        const next = signature();
        if (!next) return;
        if (next !== lastSignature) {
            lastSignature = next;
            render();
        }
    }

    function boot() {
        // Alpine can initialize just after DOMContentLoaded depending on script
        // order. A short poll is also useful because media upload and account
        // selection mutate Alpine state without replacing the whole page.
        render();
        window.setInterval(tick, 250);
        document.addEventListener('htmx:afterSwap', function () {
            lastSignature = '';
            window.setTimeout(render, 0);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot, { once: true });
    } else {
        boot();
    }
})();
