const extensionApi = globalThis.browser || globalThis.chrome;

const state = {
  capture: null,
  config: null,
  accounts: [],
  sourceTab: null,
};

const elements = {
  status: document.querySelector("#status"),
  form: document.querySelector("#draft-form"),
  success: document.querySelector("#success"),
  settings: document.querySelector("#settings"),
  platformBadge: document.querySelector("#platform-badge"),
  sourceLabel: document.querySelector("#source-label"),
  sourceUrl: document.querySelector("#source-url"),
  account: document.querySelector("#account"),
  creatorHandle: document.querySelector("#creator-handle"),
  creatorName: document.querySelector("#creator-name"),
  title: document.querySelector("#title"),
  caption: document.querySelector("#caption"),
  mediaOption: document.querySelector("#media-option"),
  mediaCount: document.querySelector("#media-count"),
  includeMedia: document.querySelector("#include-media"),
  save: document.querySelector("#save"),
  successTitle: document.querySelector("#success-title"),
  successMessage: document.querySelector("#success-message"),
  openDraft: document.querySelector("#open-draft"),
  captureAnother: document.querySelector("#capture-another"),
};

function normalizeStudioUrl(value) {
  return String(value || "https://studio.thetngame.com").trim().replace(/\/+$/, "");
}

function showStatus(message, kind = "loading") {
  elements.status.textContent = message;
  elements.status.className = `status ${kind}`;
  elements.status.hidden = false;
}

function apiHeaders(extra = {}) {
  return {
    Authorization: `Bearer ${state.config.apiKey}`,
    ...extra,
  };
}

async function apiError(response) {
  try {
    const body = await response.json();
    return body.detail || body.error || `Studio returned ${response.status}.`;
  } catch (_error) {
    return `Studio returned ${response.status}.`;
  }
}

async function loadConfig() {
  const stored = await extensionApi.storage.local.get(["studioUrl", "apiKey", "defaultAccountId"]);
  const config = {
    studioUrl: normalizeStudioUrl(stored.studioUrl),
    apiKey: String(stored.apiKey || "").trim(),
    defaultAccountId: String(stored.defaultAccountId || ""),
  };
  if (!config.apiKey) throw new Error("Connect the extension to Studio first. Open settings to add an API key.");
  state.config = config;
}

async function getSourceTab() {
  const requestedId = Number(new URLSearchParams(location.search).get("tabId"));
  if (Number.isInteger(requestedId) && requestedId > 0) return extensionApi.tabs.get(requestedId);
  const [tab] = await extensionApi.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active browser tab was found.");
  return tab;
}

async function captureCurrentPost() {
  const tab = await getSourceTab();
  if (!/^https?:/i.test(tab.url || "")) {
    throw new Error("Open an individual social post or public web page, then click the extension again.");
  }
  state.sourceTab = tab;
  await extensionApi.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  const response = await extensionApi.tabs.sendMessage(tab.id, { type: "tn-social-studio:capture" });
  if (!response?.ok) throw new Error(response?.error || "The post could not be read from this page.");
  state.capture = response.result;
}

async function loadAccounts() {
  const response = await fetch(`${state.config.studioUrl}/api/v1/me/`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(await apiError(response));
  const body = await response.json();
  state.accounts = (body.allowlisted_accounts || []).filter((account) => account.connection_status === "connected");
  if (!state.accounts.length) {
    throw new Error("This API key has no connected accounts. Update its account access in Studio.");
  }
}

function populateForm() {
  const capture = state.capture;
  elements.platformBadge.textContent = capture.sourcePlatform || "Web";
  elements.sourceLabel.textContent = capture.title || state.sourceTab?.title || "Current post";
  elements.sourceUrl.href = capture.sourceUrl;
  elements.creatorHandle.value = capture.creatorHandle ? `@${capture.creatorHandle.replace(/^@/, "")}` : "";
  elements.creatorName.value = capture.creatorName || "";
  elements.title.value = capture.title || "";
  elements.caption.value = capture.caption || "";

  elements.account.replaceChildren();
  for (const account of state.accounts) {
    const option = document.createElement("option");
    option.value = account.id;
    option.textContent = `${account.account_name} · ${account.platform.replaceAll("_", " ")}`;
    option.selected = account.id === state.config.defaultAccountId;
    elements.account.append(option);
  }

  const mediaCount = capture.media?.length || 0;
  elements.mediaOption.hidden = mediaCount === 0;
  elements.mediaCount.textContent = `Include ${mediaCount} detected media file${mediaCount === 1 ? "" : "s"}`;
  elements.form.hidden = false;
  elements.status.hidden = true;
}

function mediaOriginPattern(mediaUrl) {
  const url = new URL(mediaUrl);
  return `${url.origin}/*`;
}

async function requestMediaAccess(media) {
  const origins = [...new Set(media.map((item) => mediaOriginPattern(item.url)))];
  if (!origins.length) return true;
  try {
    return await extensionApi.permissions.request({ origins });
  } catch (_error) {
    return false;
  }
}

function extensionFor(blob, url) {
  const byType = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
  };
  const fromType = byType[blob.type.toLowerCase()];
  if (fromType) return fromType;
  const fromPath = new URL(url).pathname.match(/\.([a-z0-9]{2,5})$/i)?.[1];
  return fromPath || (blob.type.startsWith("video/") ? "mp4" : "jpg");
}

function randomIdempotencyKey() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function uploadMediaItem(item, index) {
  const mediaResponse = await fetch(item.url, { credentials: "omit", referrerPolicy: "no-referrer" });
  if (!mediaResponse.ok) throw new Error(`Media download returned ${mediaResponse.status}.`);
  const blob = await mediaResponse.blob();
  if (!/^(image|video)\//i.test(blob.type)) throw new Error("The media host did not return a photo or video.");

  const form = new FormData();
  const fileName = `browser-capture-${Date.now()}-${index + 1}.${extensionFor(blob, item.url)}`;
  form.append("file", blob, fileName);
  form.append("title", state.capture.title || "Browser capture");
  form.append("tags", `browser-capture,ugc,${state.capture.sourcePlatform || "website"}`);
  form.append("idempotency_key", randomIdempotencyKey());
  const uploadResponse = await fetch(`${state.config.studioUrl}/api/v1/media/`, {
    method: "POST",
    headers: apiHeaders(),
    body: form,
  });
  if (!uploadResponse.ok) throw new Error(await apiError(uploadResponse));
  return (await uploadResponse.json()).id;
}

async function copyDetectedMedia() {
  const media = state.capture.media || [];
  if (!elements.includeMedia.checked || !media.length) return { ids: [], failed: 0 };
  const permissionGranted = await requestMediaAccess(media);
  if (!permissionGranted) return { ids: [], failed: media.length };

  const settled = await Promise.allSettled(media.map(uploadMediaItem));
  return {
    ids: settled.filter((result) => result.status === "fulfilled").map((result) => result.value),
    failed: settled.filter((result) => result.status === "rejected").length,
  };
}

async function createDraft(mediaAssetIds) {
  const response = await fetch(`${state.config.studioUrl}/api/v1/browser-drafts/`, {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      social_account_id: elements.account.value,
      source_url: state.capture.sourceUrl,
      source_platform: state.capture.sourcePlatform,
      source_external_id: state.capture.sourceExternalId,
      creator_handle: elements.creatorHandle.value.replace(/^@/, "").trim(),
      creator_name: elements.creatorName.value.trim(),
      title: elements.title.value.trim(),
      caption: elements.caption.value.trim(),
      media_asset_ids: mediaAssetIds,
    }),
  });
  if (!response.ok) throw new Error(await apiError(response));
  return response.json();
}

async function submit(event) {
  event.preventDefault();
  elements.save.disabled = true;
  elements.save.textContent = "Adding draft…";
  showStatus("Copying the post into Studio…", "loading");
  try {
    const mediaResult = await copyDetectedMedia();
    const result = await createDraft(mediaResult.ids);
    elements.form.hidden = true;
    elements.status.hidden = true;
    elements.success.hidden = false;
    elements.successTitle.textContent = result.duplicate ? "Draft already exists" : "Draft added";
    elements.successMessage.textContent = mediaResult.failed
      ? `The caption and source were saved. ${mediaResult.failed} protected media file${mediaResult.failed === 1 ? " was" : "s were"} not copied; add that media manually in Studio.`
      : "The caption, source, creator, and available media are ready to edit in Studio.";
    elements.openDraft.href = `${state.config.studioUrl}${result.edit_path}`;
  } catch (error) {
    showStatus(error?.message || "The draft could not be created.", "error");
  } finally {
    elements.save.disabled = false;
    elements.save.textContent = "Add to TN Social Studio";
  }
}

async function initialize() {
  try {
    await loadConfig();
    await Promise.all([captureCurrentPost(), loadAccounts()]);
    populateForm();
  } catch (error) {
    showStatus(error?.message || "The extension could not start.", "error");
  }
}

elements.settings.addEventListener("click", () => extensionApi.runtime.openOptionsPage());
elements.form.addEventListener("submit", submit);
elements.captureAnother.addEventListener("click", () => location.reload());
initialize();
