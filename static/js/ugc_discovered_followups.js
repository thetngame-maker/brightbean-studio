(function () {
    const grid = document.getElementById('ugc-card-grid');
    const sortSelect = document.getElementById('ugc-sort');
    if (!grid || !sortSelect) return;

    const discoveredTab = document.querySelector('a[href*="tab=discovered"].border-violet-500');
    if (!discoveredTab) return;

    const cards = Array.from(grid.querySelectorAll('.ugc-card'));
    if (!cards.length) return;

    function formatAge(isoValue) {
        const timestamp = Date.parse(isoValue || '');
        if (!Number.isFinite(timestamp)) return '';
        const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
        if (seconds < 60) return 'just now';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        if (days < 30) return `${days}d ago`;
        const months = Math.floor(days / 30);
        return `${months}mo ago`;
    }

    function creatorHandleFor(card) {
        const contributorLine = Array.from(card.querySelectorAll('p')).find((node) => (node.textContent || '').trim().startsWith('@'));
        if (contributorLine) {
            const match = (contributorLine.textContent || '').match(/@([A-Za-z0-9._]+)/);
            if (match) return match[1];
        }
        const match = (card.textContent || '').match(/@([A-Za-z0-9._]+)/);
        return match ? match[1] : '';
    }

    function titleFor(card) {
        const heading = card.querySelector('h2');
        return heading ? (heading.textContent || '').trim() : 'your post';
    }

    function followupMessage(card) {
        const handle = creatorHandleFor(card);
        const title = titleFor(card);
        const greeting = handle ? `Hi @${handle}!` : 'Hi!';
        return `${greeting} Just following up on our message about your ${title} post. We’d still love to feature it on The TN Game’s social media and website with full credit and a link back to your original post. If you’re okay with us sharing it, please reply YES. Thank you!`;
    }

    async function copyText(text) {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return;
        }
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand('copy');
        textarea.remove();
        if (!copied) throw new Error('Copy failed');
    }

    function addRequestedTools(card, item) {
        if (item.permission_status !== 'requested') return;
        const requestIso = item.requested_at || item.permission_updated_at || '';
        const timestamp = Date.parse(requestIso);
        card.dataset.permissionRequestedAt = Number.isFinite(timestamp) ? String(timestamp) : '0';

        const permissionForm = card.querySelector('form[action*="/permission/"]');
        const panel = permissionForm ? permissionForm.closest('div.mt-3') : null;
        if (!panel) return;

        const statusText = Array.from(panel.querySelectorAll('div')).find((node) => (node.textContent || '').trim() === 'Permission requested');
        if (statusText && requestIso && !panel.querySelector('.ugc-requested-age')) {
            const age = document.createElement('div');
            age.className = 'ugc-requested-age text-[10px] text-amber-700 mt-1';
            age.textContent = `Requested ${formatAge(requestIso)}`;
            age.title = new Date(requestIso).toLocaleString();
            statusText.insertAdjacentElement('afterend', age);
        }

        if (statusText && item.followup_count && !panel.querySelector('.ugc-followup-history')) {
            const history = document.createElement('div');
            history.className = 'ugc-followup-history text-[10px] text-violet-700 mt-1';
            const noun = Number(item.followup_count) === 1 ? 'follow-up' : 'follow-ups';
            const last = item.last_followup_at ? ` · last sent ${formatAge(item.last_followup_at)}` : '';
            history.textContent = `${item.followup_count} ${noun}${last}`;
            if (item.last_followup_at) history.title = new Date(item.last_followup_at).toLocaleString();
            statusText.parentElement.appendChild(history);
        }

        const actions = panel.querySelector('.flex.flex-wrap.gap-1\\.5');
        if (!actions) return;

        if (!panel.querySelector('.ugc-copy-followup')) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'ugc-copy-followup px-2.5 py-1.5 text-[11px] font-semibold rounded-lg border border-violet-200 bg-white text-violet-700 hover:bg-violet-50 cursor-pointer';
            button.textContent = 'Copy follow-up';
            button.title = followupMessage(card);
            actions.insertBefore(button, actions.firstChild);

            button.addEventListener('click', async function () {
                const original = button.textContent;
                try {
                    await copyText(followupMessage(card));
                    button.textContent = 'Copied';
                    button.classList.add('text-emerald-700', 'border-emerald-200', 'bg-emerald-50');
                    window.setTimeout(() => {
                        button.textContent = original;
                        button.classList.remove('text-emerald-700', 'border-emerald-200', 'bg-emerald-50');
                    }, 1800);
                } catch (error) {
                    button.textContent = 'Copy failed';
                    window.setTimeout(() => { button.textContent = original; }, 1800);
                }
            });
        }

        if (!panel.querySelector('.ugc-log-followup')) {
            const id = card.dataset.submissionId;
            const csrf = permissionForm.querySelector('input[name="csrfmiddlewaretoken"]');
            if (id && csrf) {
                const form = document.createElement('form');
                form.method = 'post';
                form.action = window.location.pathname.replace(/\/?$/, `/${id}/permission/followup/`);
                form.className = 'inline-flex';
                form.innerHTML = `<input type="hidden" name="csrfmiddlewaretoken" value="${csrf.value}"><input type="hidden" name="channel" value="${card.dataset.source || 'manual'}"><button type="submit" class="ugc-log-followup px-2.5 py-1.5 text-[11px] font-semibold rounded-lg border border-violet-200 bg-violet-50 text-violet-700 hover:bg-violet-100 cursor-pointer">Mark follow-up sent</button>`;
                const copyButton = actions.querySelector('.ugc-copy-followup');
                if (copyButton && copyButton.nextSibling) actions.insertBefore(form, copyButton.nextSibling);
                else if (copyButton) actions.insertBefore(form, copyButton.nextSibling);
                else actions.insertBefore(form, actions.firstChild);
            }
        }
    }

    function sortOldestRequest() {
        if (sortSelect.value !== 'oldest_request') return;
        cards.slice().sort((a, b) => {
            const aTime = Number(a.dataset.permissionRequestedAt || 0);
            const bTime = Number(b.dataset.permissionRequestedAt || 0);
            if (aTime && bTime) return aTime - bTime;
            if (aTime) return -1;
            if (bTime) return 1;
            return Number(b.dataset.submitted || 0) - Number(a.dataset.submitted || 0);
        }).forEach((card) => grid.appendChild(card));
    }

    if (!sortSelect.querySelector('option[value="oldest_request"]')) {
        const option = document.createElement('option');
        option.value = 'oldest_request';
        option.textContent = 'Oldest request';
        sortSelect.appendChild(option);
    }

    sortSelect.addEventListener('change', () => window.setTimeout(sortOldestRequest, 0));

    const intelligenceUrl = window.location.pathname.replace(/\/?$/, '/discovered/intelligence/');
    fetch(intelligenceUrl, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error('Follow-up timing unavailable')))
        .then((payload) => {
            (payload.items || []).forEach((item) => {
                const card = cards.find((candidate) => candidate.dataset.submissionId === item.id);
                if (card) addRequestedTools(card, item);
            });
            sortOldestRequest();
        })
        .catch(() => {});
})();
