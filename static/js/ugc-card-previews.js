/* Load visible previews from durable storage; repair expired imports once. */
(() => {
    const queue = [];
    let active = 0;
    const csrf = document.querySelector('[name="csrfmiddlewaretoken"]')?.value;
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    async function load(box) {
        const label = box.querySelector('.ugc-preview-state');
        const button = box.querySelector('button');
        const img = box.querySelector('img');
        try {
            let response = await fetch(box.dataset.endpoint);
            let data = await response.json();
            if (data.status !== 'ready') {
                label.textContent = 'Restoring preview…';
                response = await fetch(box.dataset.endpoint, {method:'POST', headers:{'X-CSRFToken':csrf}});
                data = await response.json();
                for (let attempt = 0; data.status === 'loading' && attempt < 45; attempt++) {
                    await wait(4000);
                    data = await (await fetch(box.dataset.endpoint)).json();
                }
            }
            if (data.status !== 'ready') throw new Error('unavailable');
            button.dataset.previewSrc = data.url;
            button.dataset.previewType = data.type;
            await new Promise((resolve, reject) => {
                img.onload = resolve;
                img.onerror = reject;
                img.src = box.dataset.endpoint + '?image=1';
            });
            img.hidden = false;
            button.disabled = false;
            label.textContent = data.type === 'video' ? '▶ Preview video' : 'Preview photo';
            label.style.cssText = 'inset:auto 8px 8px auto;background:#000a;color:white;padding:4px 8px;border-radius:6px';
        } catch (_) {
            label.textContent = 'Preview unavailable · Open “View source” below';
            img.hidden = true;
            button.disabled = true;
        }
    }
    function pump() {
        while (active < 2 && queue.length) {
            const box = queue.shift();
            if (box.closest('.ugc-card')?.classList.contains('hidden')) { observer.observe(box); continue; }
            active++;
            load(box).finally(() => { active--; pump(); });
        }
    }
    const observer = new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            observer.unobserve(entry.target);
            queue.push(entry.target);
        });
        pump();
    }, {rootMargin:'100px'});
    document.querySelectorAll('.ugc-durable-preview').forEach(box => observer.observe(box));
})();
