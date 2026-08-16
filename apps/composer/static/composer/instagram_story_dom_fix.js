/* TN Social Studio — live-media bridge for Instagram Story controls.
 *
 * BrightBean mutates #media-list outside Alpine after uploads/imports. The
 * Story extension originally read composerApp.mediaItems, which can remain at
 * its server-rendered value and therefore report zero media even while a
 * thumbnail/video is visible. This bridge uses the same live DOM source the
 * upstream composer uses for video detection.
 */
(function () {
    'use strict';

    const ROOT = '#tn-instagram-story-controls';
    const MEDIA = '#media-list';

    function realMedia() {
        const list = document.querySelector(MEDIA);
        if (!list) return [];
        return Array.from(list.querySelectorAll(':scope > .media-thumb')).filter(function (thumb) {
            return !!thumb.querySelector('video, img');
        });
    }

    function setButtonEnabled(button, enabled) {
        if (!button) return;
        button.disabled = !enabled;
        button.toggleAttribute('disabled', !enabled);
        button.classList.toggle('opacity-45', !enabled);
        button.classList.toggle('cursor-not-allowed', !enabled);
        button.classList.toggle('cursor-pointer', enabled);
    }

    function setPrimaryLabel(button, isVideo) {
        if (!button) return;
        const label = isVideo ? 'Reel' : 'Post';
        const sub = isVideo ? 'Instagram Reel' : 'Feed post';
        const textNode = Array.from(button.childNodes).find(function (node) {
            return node.nodeType === Node.TEXT_NODE && node.textContent.trim();
        });
        if (textNode) textNode.textContent = '\n                            ' + label + '\n                            ';
        const span = button.querySelector('span');
        if (span) span.textContent = sub;
    }

    function syncStoryPreview(mediaEl) {
        const preview = document.getElementById('tn-instagram-story-preview');
        if (!preview || !mediaEl) return;
        const frame = preview.querySelector('.tn-story-frame');
        if (!frame) return;
        const source = mediaEl.querySelector('video, img');
        if (!source || !source.src) return;

        const existingMedia = frame.querySelector('video, img');
        if (existingMedia && existingMedia.src === source.src) return;

        const top = frame.querySelector('.tn-story-top');
        frame.querySelectorAll('video, img, .tn-story-empty').forEach(function (el) { el.remove(); });
        let clone;
        if (source.tagName === 'VIDEO') {
            clone = document.createElement('video');
            clone.src = source.src;
            clone.muted = true;
            clone.controls = true;
            clone.playsInline = true;
            clone.preload = 'metadata';
        } else {
            clone = document.createElement('img');
            clone.src = source.src;
            clone.alt = 'Story preview';
        }
        if (top) frame.insertBefore(clone, top);
        else frame.appendChild(clone);
    }

    function sync() {
        const controls = document.querySelector(ROOT);
        if (!controls) return;

        const media = realMedia();
        const count = media.length;
        const one = count === 1;
        const isVideo = one && !!media[0].querySelector('video');

        controls.querySelectorAll('.tn-ig-story-card').forEach(function (card) {
            const primary = card.querySelector('[data-action="primary"]');
            const story = card.querySelector('[data-action="story"]');
            const also = card.querySelector('[data-action="also-story"]');
            const formatInput = card.querySelector('[data-role="format-input"]');
            const alsoInput = card.querySelector('[data-role="also-input"]');

            // For one real attached photo/video, Story must be selectable even
            // when Alpine's server-rendered mediaItems array is stale.
            if (one) {
                setButtonEnabled(primary, true);
                setButtonEnabled(story, true);
                setPrimaryLabel(primary, isVideo);

                const storySelected = formatInput && formatInput.value === 'story';
                if (also) {
                    also.disabled = storySelected;
                    also.toggleAttribute('disabled', storySelected);
                    const label = also.closest('label');
                    if (label) label.classList.toggle('opacity-50', storySelected);
                }

                // A lone video defaults to Reel, not an image feed post. Do not
                // overwrite an explicit Story choice.
                if (formatInput && !storySelected && isVideo && formatInput.value !== 'reel') {
                    formatInput.value = 'reel';
                }
                if (formatInput && !storySelected && !isVideo && formatInput.value === 'reel') {
                    formatInput.value = 'image';
                }

                if (storySelected && alsoInput) alsoInput.value = 'false';
                if (storySelected) syncStoryPreview(media[0]);
            }
        });
    }

    function boot() {
        sync();
        window.setInterval(sync, 150);
        document.body.addEventListener('previewUpdate', sync);
        document.body.addEventListener('htmx:afterSwap', function () {
            window.setTimeout(sync, 0);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot, { once: true });
    } else {
        boot();
    }
})();
