/* Load visible previews from durable storage; repair expired imports once. */
(() => {
    const queue = [];
    let active = 0;
    const csrf = document.querySelector('[name="csrfmiddlewaretoken"]')?.value;
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    async function request(url, options = {}) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 12000);
        try {
            const response = await fetch(url, {...options, signal: controller.signal});
            if (!response.ok) throw new Error('request failed');
            return await response.json();
        } finally { clearTimeout(timer); }
    }
    async function load(box) {
        const label = box.querySelector('.ugc-preview-state');
        const button = box.querySelector('button');
        const img = box.querySelector('img');
        box.querySelector('.ugc-preview-retry')?.remove();
        label.style.cssText = '';
        label.textContent = 'Loading preview…';
        try {
            let data = await request(box.dataset.endpoint);
            if (data.status !== 'ready') {
                label.textContent = 'Restoring preview…';
                data = await request(box.dataset.endpoint, {method:'POST', headers:{'X-CSRFToken':csrf}});
                for (let attempt = 0; data.status === 'loading' && attempt < 12; attempt++) {
                    await wait(4000);
                    data = await request(box.dataset.endpoint);
                }
            }
            if (data.status !== 'ready') throw new Error('unavailable');
            button.dataset.previewSrc = data.url;
            button.dataset.previewType = data.type;
            await new Promise((resolve, reject) => {
                const timer = setTimeout(() => { img.removeAttribute('src'); reject(new Error('image timeout')); }, 12000);
                img.onload = () => { clearTimeout(timer); resolve(); };
                img.onerror = () => { clearTimeout(timer); reject(new Error('image failed')); };
                img.src = data.thumbnail || (box.dataset.endpoint + '?image=1');
            });
            img.hidden = false;
            button.disabled = false;
            label.textContent = data.type === 'video' ? '▶ Preview video' : 'Preview photo';
            label.style.cssText = 'inset:auto 8px 8px auto;background:#000a;color:white;padding:4px 8px;border-radius:6px';
        } catch (_) {
            label.textContent = 'Preview could not load. Try again or view the source below.';
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'ugc-preview-retry';
            retry.textContent = 'Retry preview';
            retry.style.cssText = 'position:absolute;bottom:24px;left:50%;transform:translateX(-50%);padding:8px 14px;background:white;border:1px solid #ddd;border-radius:8px';
            retry.addEventListener('click', () => { retry.remove(); queue.push(box); pump(); });
            box.appendChild(retry);
            img.hidden = true;
            button.disabled = true;
        }
    }
    function pump() {
        while (active < 6 && queue.length) {
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
