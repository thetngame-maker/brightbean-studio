(function () {
    "use strict";

    function csrfToken() {
        return document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
    }

    function captionTextarea() {
        return document.querySelector('textarea[name="caption"]');
    }

    function setCaption(value) {
        const textarea = captionTextarea();
        if (!textarea) return;
        textarea.value = value;
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        textarea.focus();
    }

    function storageKey(button) {
        return `tn-caption-ai:${button.dataset.postId || window.location.pathname}`;
    }

    function loadHistory(button) {
        try {
            return JSON.parse(window.sessionStorage.getItem(storageKey(button)) || "null");
        } catch (_error) {
            return null;
        }
    }

    function saveHistory(button, history) {
        try {
            window.sessionStorage.setItem(storageKey(button), JSON.stringify(history));
        } catch (_error) {
            // The comparison still works in-memory when browser storage is unavailable.
        }
    }

    function buildDialog() {
        const overlay = document.createElement("div");
        overlay.className = "caption-ai-overlay";
        overlay.hidden = true;
        overlay.innerHTML = `
            <section class="caption-ai-dialog" role="dialog" aria-modal="true" aria-labelledby="caption-ai-title">
                <header class="caption-ai-dialog-header">
                    <div>
                        <h2 id="caption-ai-title">Compare captions</h2>
                        <p>Your previous caption stays unchanged until you use the new version.</p>
                    </div>
                    <button type="button" class="caption-ai-close" aria-label="Close">×</button>
                </header>
                <div class="caption-ai-compare">
                    <div class="caption-ai-column">
                        <label for="caption-ai-previous">Previous caption</label>
                        <textarea id="caption-ai-previous" readonly></textarea>
                    </div>
                    <div class="caption-ai-column">
                        <label for="caption-ai-suggestion">AI suggestion — editable</label>
                        <textarea id="caption-ai-suggestion"></textarea>
                    </div>
                </div>
                <p class="caption-ai-warning"></p>
                <footer class="caption-ai-dialog-footer">
                    <button type="button" class="caption-ai-secondary">Keep previous</button>
                    <button type="button" class="caption-ai-primary">Use improved caption</button>
                </footer>
            </section>`;
        document.body.appendChild(overlay);
        return overlay;
    }

    function closeDialog(dialog) {
        dialog.hidden = true;
        document.body.style.overflow = "";
    }

    function openDialog(dialog, previous, suggestion, options) {
        const previousField = dialog.querySelector("#caption-ai-previous");
        const suggestionField = dialog.querySelector("#caption-ai-suggestion");
        const warning = dialog.querySelector(".caption-ai-warning");
        previousField.value = previous || "";
        suggestionField.value = suggestion || "";
        warning.textContent = options?.warning || "";
        warning.classList.toggle("is-visible", Boolean(options?.warning));
        dialog.hidden = false;
        document.body.style.overflow = "hidden";
        dialog.querySelector(".caption-ai-primary").onclick = function () {
            options.onApply(suggestionField.value);
            closeDialog(dialog);
        };
        dialog.querySelector(".caption-ai-secondary").onclick = function () {
            closeDialog(dialog);
        };
        window.setTimeout(() => suggestionField.focus(), 0);
    }

    function renderHistory(button, dialog, history) {
        let row = document.querySelector(".caption-ai-history");
        if (!history) {
            row?.remove();
            return;
        }
        if (!row) {
            row = document.createElement("div");
            row.className = "caption-ai-history";
            captionTextarea()?.closest(".caption-wrap")?.insertAdjacentElement("beforebegin", row);
        }
        row.innerHTML = '<span>AI caption applied.</span><button type="button" data-action="view">View previous</button><button type="button" data-action="undo">Undo</button>';
        row.querySelector('[data-action="view"]').onclick = function () {
            openDialog(dialog, history.previous, captionTextarea()?.value || history.improved, {
                onApply: function (value) {
                    history.improved = value;
                    saveHistory(button, history);
                    setCaption(value);
                },
            });
        };
        row.querySelector('[data-action="undo"]').onclick = function () {
            setCaption(history.previous);
            window.sessionStorage.removeItem(storageKey(button));
            renderHistory(button, dialog, null);
        };
    }

    function init() {
        const button = document.getElementById("caption-ai-improve");
        const textarea = captionTextarea();
        if (!button || !textarea) return;
        const dialog = buildDialog();
        let history = loadHistory(button);
        renderHistory(button, dialog, history);

        document.body.addEventListener("autosaved", function (event) {
            const postId = event.detail?.postId;
            if (!postId || button.dataset.postId === String(postId)) return;
            const oldKey = storageKey(button);
            button.dataset.postId = String(postId);
            if (history) {
                try {
                    window.sessionStorage.removeItem(oldKey);
                } catch (_error) {
                    // Keep the in-memory history when browser storage is unavailable.
                }
                saveHistory(button, history);
            }
        });

        dialog.querySelector(".caption-ai-close").onclick = () => closeDialog(dialog);
        dialog.addEventListener("click", function (event) {
            if (event.target === dialog) closeDialog(dialog);
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !dialog.hidden) closeDialog(dialog);
        });

        button.addEventListener("click", async function () {
            const previous = textarea.value.trim();
            const errorHost = button.parentElement.querySelector(".caption-ai-error") || document.createElement("p");
            errorHost.className = "caption-ai-error";
            if (!errorHost.parentElement) button.parentElement.appendChild(errorHost);
            errorHost.textContent = "";
            if (!previous) {
                errorHost.textContent = "Add a caption first, then improve it with AI.";
                return;
            }

            button.disabled = true;
            const originalButtonText = button.querySelector("span").textContent;
            button.querySelector("span").textContent = "Improving…";
            const form = button.closest("form") || document.querySelector("form");
            const body = new URLSearchParams({
                caption: textarea.value,
                title: form?.querySelector('[name="title"]')?.value || "",
                selected_accounts: form?.querySelector('[name="selected_accounts"]')?.value || "",
                post_id: button.dataset.postId || "",
            });
            try {
                const response = await fetch(button.dataset.url, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-CSRFToken": csrfToken(),
                    },
                    body,
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || "AI caption improvement failed.");
                const warning = payload.over_limit
                    ? `The suggestion is longer than the ${payload.target_length}-character limit for one selected destination. Edit it before applying.`
                    : "";
                openDialog(dialog, payload.previous_caption, payload.suggested_caption, {
                    warning,
                    onApply: function (value) {
                        history = { previous: payload.previous_caption, improved: value, appliedAt: Date.now() };
                        saveHistory(button, history);
                        setCaption(value);
                        renderHistory(button, dialog, history);
                    },
                });
            } catch (error) {
                errorHost.textContent = error.message || "AI could not improve this caption right now.";
            } finally {
                button.disabled = false;
                button.querySelector("span").textContent = originalButtonText;
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
