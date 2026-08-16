/* TN Social Studio — Instagram Story composer controls.
 *
 * Adds an explicit Feed/Reel vs Story selector for Instagram Login accounts,
 * plus an "Also add to Story" option for a single photo/video. The server
 * persists these controls into PlatformPost.platform_extra.
 */
(function () {
    'use strict';

    const ROOT_SELECTOR = '.composer-main';
    const CONTAINER_ID = 'tn-instagram-story-controls';
    const STORY_PREVIEW_ID = 'tn-instagram-story-preview';
    const STYLE_ID = 'tn-instagram-story-style';
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
            first,
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
            .replace(/\"/g, '&quot;')
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
        const noMedia = media.count === 0;
        const multiple = media.count > 1;
        const primaryLabel = multiple ? 'Carousel' : (media.isVideo ? 'Reel' : 'Post');
        const primarySubLabel = multiple ? 'Feed carousel' : (media.isVideo ? 'Instagram Reel' : 'Feed post');
        const primaryDisabled = noMedia;
        const storyDisabled = noMedia || multiple;
        const alsoDisabled = noMedia || multiple || state.format === 'story';

        let mediaHint = '';
        if (noMedia) mediaHint = 'Add one photo or video to choose how Instagram should publish it.';
        else if (multiple) mediaHint = 'Multiple files publish as a carousel. Story mode is available for a single photo or video.';

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
                            ${primaryLabel}
                            <span class="block text-[10px] font-normal mt-0.5 opacity-70">${primarySubLabel}</span>
                        </button>
                        <button type="button" data-action="story" ${storyDisabled ? 'disabled' : ''}
                                class="tn-ig-format-btn px-3 py-2 rounded-lg border text-sm font-semibold transition-all ${buttonClass(state.format === 'story')} ${storyDisabled ? 'opacity-45 cursor-not-allowed' : 'cursor-pointer'}">
                            Story
                            <span class="block text-[10px] font-normal mt-0.5 opacity-70">Story only</span>
                        </button>
                    </div>
                    ${mediaHint ? `<p class="text-[11px] text-stone-400 mt-1.5">${escapeHtml(mediaHint)}</p>` : ''}
                </div>

                <div class="border-t border-stone-100 pt-3 ${state.format === 'story' ? 'hidden' : ''}">
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
                    ${multiple ? '<p class="text-[11px] text-amber-600 mt-1">“Also add to Story” currently supports one photo or one video at a time.</p>' : ''}
                </div>

                ${state.format === 'story' ? '<div class="text-[11px] text-stone-500 bg-stone-50 rounded-lg px-3 py-2">Story publishing uses the media itself. The caption is retained in your draft/history but is not placed onto the Story.</div>' : ''}
            </section>`;
    }

    function mediaUrl(item) {
        if (!item) return '';
        return item.preview_url || item.thumbnail_url || item.file_url || item.url || item.media_url || item.src || '';
    }

    function ensureStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = `
            #${STORY_PREVIEW_ID}{padding:16px 12px 24px;display:flex;flex-direction:column;align-items:center;gap:10px}
            #${STORY_PREVIEW_ID} .tn-story-label{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#78716c}
            #${STORY_PREVIEW_ID} .tn-story-frame{position:relative;width:min(250px,88%);aspect-ratio:9/16;border-radius:20px;overflow:hidden;background:#111;box-shadow:0 8px 28px rgba(0,0,0,.14)}
            #${STORY_PREVIEW_ID} .tn-story-frame img,#${STORY_PREVIEW_ID} .tn-story-frame video{width:100%;height:100%;object-fit:cover;display:block}
            #${STORY_PREVIEW_ID} .tn-story-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#d6d3d1;font-size:13px;text-align:center;padding:24px}
            #${STORY_PREVIEW_ID} .tn-story-top{position:absolute;top:0;left:0;right:0;padding:12px;z-index:2;background:linear-gradient(to bottom,rgba(0,0,0,.45),transparent);color:white;font-size:12px;font-weight:600}
            #${STORY_PREVIEW_ID} .tn-story-note{font-size:11px;color:#a8a29e;text-align:center;max-width:260px}
        `;
        document.head.appendChild(style);
    }

    function findPreviewPane(root) {
        let parent = root.parentElement;
        for (let depth = 0; depth < 4 && parent; depth += 1, parent = parent.parentElement) {
            const children = Array.from(parent.children || []);
            const sibling = children.find(el => el !== root && /\bpreview\b/i.test((el.textContent || '').slice(0, 160)));
            if (sibling) return sibling;
        }
        const candidates = Array.from(document.querySelectorAll('aside, [class*="preview"], [id*="preview"]'));
        return candidates.find(el => /\bpreview\b/i.test((el.textContent || '').slice(0, 160))) || null;
    }

    function updateStoryPreview(root, app, accounts, media) {
        ensureStyles();
        const storyEntry = accounts.find(entry => {
            const state = stateByAccount.get(entry.id);
            return state && state.format === 'story';
        });
        const existing = document.getElementById(STORY_PREVIEW_ID);
        if (!storyEntry) {
            if (existing) existing.remove();
            return;
        }

        const pane = findPreviewPane(root);
        if (!pane) return;
        let preview = existing;
        if (!preview) {
            preview = document.createElement('div');
            preview.id = STORY_PREVIEW_ID;
            pane.appendChild(preview);
        }

        const url = mediaUrl(media.first);
        const accountName = escapeHtml((storyEntry.meta && storyEntry.meta.name) || 'Instagram');
        let mediaHtml = '<div class="tn-story-empty">9:16 Story preview</div>';
        if (url && media.isVideo) {
            mediaHtml = `<video src="${escapeHtml(url)}" muted playsinline controls preload="metadata"></video>`;
        } else if (url) {
            mediaHtml = `<img src="${escapeHtml(url)}" alt="Story preview">`;
        }

        preview.innerHTML = `
            <div class="tn-story-label">Story preview</div>
            <div class="tn-story-frame">
                ${mediaHtml}
                <div class="tn-story-top">${accountName}</div>
            </div>
            <div class="tn-story-note">Stories publish vertically at 9:16. Captions are not overlaid onto the Story media.</div>
        `;
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
            const preview = document.getElementById(STORY_PREVIEW_ID);
            if (preview) preview.remove();
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
        updateStoryPreview(root, app, accounts, media);
    }

    function signature() {
        const root = document.querySelector(ROOT_SELECTOR);
        const app = root ? alpineData(root) : null;
        if (!app) return '';
        const selected = Array.isArray(app.selectedAccounts) ? app.selectedAccounts.join(',') : '';
        const media = Array.isArray(app.mediaItems)
            ? app.mediaItems.map(item => `${item.id || item.asset_id || ''}:${item.is_video ? 'v' : 'i'}`).join(',')
            : '';
        const formats = Array.from(stateByAccount.entries()).map(([id, state]) => `${id}:${state.format}:${state.alsoStory ? 1 : 0}`).join(',');
        return selected + '|' + media + '|' + formats;
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