/* Delegated controls survive uploads and HTMX attachment-list replacements. */
(() => {
    const list = document.getElementById('media-list');
    if (!list) return;
    const locked = list.dataset.reorderDisabled === 'true';
    let busy = false, dragged = null, opener = null, selectedId = null;
    const items = () => [...list.querySelectorAll('[data-media-id]')];
    const notice = document.createElement('p');
    notice.setAttribute('role', 'status');
    notice.className = 'px-4 text-xs text-stone-500';
    list.parentElement.after(notice);
    const dialog = document.createElement('dialog');
    dialog.setAttribute('aria-label', 'Media preview');
    dialog.style.cssText = 'padding:16px;border-radius:12px;width:min(960px,94vw);max-height:92vh;background:#1c1917;color:white;';
    dialog.innerHTML = '<div style="display:flex;gap:16px;align-items:center;margin-bottom:12px"><button type="button" data-action="previous" aria-label="Previous media">← Previous</button><span style="flex:1" data-caption></span><button type="button" data-action="next" aria-label="Next media">Next →</button><button type="button" data-action="close" aria-label="Close preview">Close ✕</button></div><div data-content style="display:flex;justify-content:center"></div><p data-error role="status"></p>';
    document.body.append(dialog);
    const content = dialog.querySelector('[data-content]');
    const close = () => dialog.close();
    dialog.addEventListener('close', () => { content.replaceChildren(); opener?.focus(); });
    dialog.addEventListener('click', event => {
        const action = event.target.closest('[data-action]')?.dataset.action;
        if (action === 'close' || event.target === dialog) close();
        if (action === 'previous') navigate(-1);
        if (action === 'next') navigate(1);
    });
    function navigate(delta) {
        const all = items();
        const index = all.findIndex(item => item.dataset.mediaId === selectedId);
        if (all[index + delta]) preview(all[index + delta]);
    }
    function preview(item) {
        selectedId = item.dataset.mediaId;
        const all = items(), index = all.indexOf(item);
        dialog.querySelector('[data-caption]').textContent = `${index + 1} / ${all.length} · ${item.dataset.mediaFilename || 'Media'}`;
        dialog.querySelector('[data-action="previous"]').disabled = index === 0;
        dialog.querySelector('[data-action="next"]').disabled = index === all.length - 1;
        dialog.querySelector('[data-error]').textContent = '';
        const media = document.createElement(item.querySelector('video') ? 'video' : 'img');
        media.style.cssText = 'max-width:100%;max-height:72vh;object-fit:contain';
        if (media.tagName === 'VIDEO') { media.controls = true; media.playsInline = true; media.preload = 'metadata'; }
        else media.alt = item.dataset.mediaFilename || 'Photo preview';
        media.addEventListener('error', () => { dialog.querySelector('[data-error]').textContent = 'This media could not be loaded. Close the preview and try again.'; });
        media.src = item.dataset.previewUrl;
        content.replaceChildren(media);
        if (!dialog.open) dialog.showModal();
    }
    function decorate() {
        items().forEach((item, index, all) => {
            item.draggable = !busy && !locked;
            item.tabIndex = 0;
            item.setAttribute('aria-label', `Preview ${item.dataset.mediaFilename || 'media'}, item ${index + 1}`);
            if (!item.querySelector('.media-order-controls')) {
                const controls = document.createElement('div');
                controls.className = 'media-order-controls';
                controls.innerHTML = '<button type="button" data-move="-1" aria-label="Move media earlier">←</button><span data-position></span><button type="button" data-move="1" aria-label="Move media later">→</button>';
                item.append(controls);
            }
            item.querySelector('[data-position]').textContent = index + 1;
            item.querySelector('[data-move="-1"]').disabled = busy || locked || index === 0;
            item.querySelector('[data-move="1"]').disabled = busy || locked || index === all.length - 1;
        });
    }
    async function move(item, target) {
        const before = items(), from = before.indexOf(item);
        if (busy || locked || from < 0 || target < 0 || target >= before.length || target === from) return;
        busy = true;
        list.insertBefore(item, target > from ? before[target].nextSibling : before[target]);
        decorate();
        notice.textContent = 'Saving media order…';
        const data = new FormData();
        items().forEach(thumb => data.append('media_order', thumb.dataset.mediaId));
        data.set('post_id', document.querySelector('[name="_autosave_post_id"]')?.value || '');
        data.set('csrfmiddlewaretoken', document.querySelector('[name="csrfmiddlewaretoken"]').value);
        try {
            const response = await fetch(list.dataset.reorderUrl, { method: 'POST', body: data });
            if (!response.ok) throw new Error('Could not save media order. Please refresh the editor and try again.');
            notice.textContent = 'Media order saved.';
            document.body.dispatchEvent(new CustomEvent('previewUpdate'));
        } catch (error) {
            before.forEach(thumb => { if (thumb.parentElement === list) list.append(thumb); });
            notice.textContent = error.message;
        } finally { busy = false; decorate(); }
    }
    list.addEventListener('click', event => {
        const item = event.target.closest('[data-media-id]');
        if (!item) return;
        const button = event.target.closest('button');
        if (button?.hasAttribute('data-move')) { move(item, items().indexOf(item) + Number(button.dataset.move)); return; }
        if (button) return;
        opener = item; preview(item);
    });
    list.addEventListener('keydown', event => {
        if (event.target.matches('[data-media-id]') && ['Enter', ' '].includes(event.key)) {
            event.preventDefault(); opener = event.target; preview(event.target);
        }
    });
    list.addEventListener('dragstart', event => {
        dragged = event.target.closest('[data-media-id]');
        if (busy || locked || !dragged) { event.preventDefault(); return; }
        event.dataTransfer.setData('text/plain', dragged.dataset.mediaId);
        event.dataTransfer.effectAllowed = 'move';
    });
    list.addEventListener('dragover', event => { if (dragged) event.preventDefault(); });
    list.addEventListener('drop', event => {
        if (!dragged) return;
        event.preventDefault(); event.stopPropagation();
        const target = event.target.closest('[data-media-id]');
        if (target) move(dragged, items().indexOf(target));
        dragged = null;
    });
    list.addEventListener('dragend', () => { dragged = null; });
    // Only child-list changes matter; disconnect while decorating to avoid observer loops.
    const observer = new MutationObserver(() => { observer.disconnect(); decorate(); observer.observe(list, {childList: true, subtree: true}); });
    decorate(); observer.observe(list, {childList: true, subtree: true});
})();
