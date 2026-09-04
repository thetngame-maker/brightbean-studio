const extensionApi = globalThis.browser || globalThis.chrome;

const studioUrl = document.querySelector("#studio-url");
const apiKey = document.querySelector("#api-key");
const defaultAccount = document.querySelector("#default-account");
const form = document.querySelector("#settings-form");
const testButton = document.querySelector("#test");
const status = document.querySelector("#status");

function normalizeUrl(value) {
  return String(value || "https://studio.thetngame.com").trim().replace(/\/+$/, "");
}

function showStatus(message, kind) {
  status.textContent = message;
  status.className = kind;
  status.hidden = false;
}

async function ensureStudioPermission(url) {
  const parsed = new URL(url);
  if (parsed.origin === "https://studio.thetngame.com" || ["localhost", "127.0.0.1"].includes(parsed.hostname)) {
    return true;
  }
  return extensionApi.permissions.request({ origins: [`${parsed.origin}/*`] });
}

async function readConnection() {
  const url = normalizeUrl(studioUrl.value);
  const token = apiKey.value.trim();
  if (!token) throw new Error("Enter a Studio API key first.");
  if (!(await ensureStudioPermission(url))) throw new Error("Browser access to this Studio address was not granted.");
  const response = await fetch(`${url}/api/v1/me/`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    let detail = `Studio returned ${response.status}.`;
    try {
      const body = await response.json();
      detail = body.detail || body.error || detail;
    } catch (_error) {
      // Keep the status-based fallback.
    }
    throw new Error(detail);
  }
  return response.json();
}

function populateAccounts(accounts, selectedId = "") {
  defaultAccount.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "Choose when capturing";
  defaultAccount.append(empty);
  for (const account of accounts || []) {
    const option = document.createElement("option");
    option.value = account.id;
    option.textContent = `${account.account_name} · ${account.platform.replaceAll("_", " ")}`;
    option.selected = account.id === selectedId;
    defaultAccount.append(option);
  }
}

async function testConnection() {
  testButton.disabled = true;
  testButton.textContent = "Testing…";
  try {
    const connection = await readConnection();
    populateAccounts(connection.allowlisted_accounts, defaultAccount.value);
    const hasCreate = (connection.permissions || []).includes("create_posts");
    const hasUpload = (connection.permissions || []).includes("upload_media");
    if (!hasCreate) throw new Error("Connected, but this key needs the Create posts permission.");
    const mediaNote = hasUpload ? "Media uploads are enabled." : "Add Upload media permission to copy photos and videos.";
    showStatus(`Connected to ${connection.workspace_name}. ${mediaNote}`, hasUpload ? "success" : "error");
    return connection;
  } catch (error) {
    showStatus(error?.message || "Connection failed.", "error");
    return null;
  } finally {
    testButton.disabled = false;
    testButton.textContent = "Test connection";
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const connection = await testConnection();
  if (!connection || !(connection.permissions || []).includes("create_posts")) return;
  await extensionApi.storage.local.set({
    studioUrl: normalizeUrl(studioUrl.value),
    apiKey: apiKey.value.trim(),
    defaultAccountId: defaultAccount.value,
  });
  showStatus(`Saved. The extension is connected to ${connection.workspace_name}.`, "success");
}

async function initialize() {
  const stored = await extensionApi.storage.local.get(["studioUrl", "apiKey", "defaultAccountId"]);
  studioUrl.value = normalizeUrl(stored.studioUrl);
  apiKey.value = stored.apiKey || "";
  if (!stored.apiKey) return;
  try {
    const connection = await readConnection();
    populateAccounts(connection.allowlisted_accounts, stored.defaultAccountId || "");
  } catch (error) {
    showStatus(error?.message || "Saved connection could not be loaded.", "error");
  }
}

testButton.addEventListener("click", testConnection);
form.addEventListener("submit", saveSettings);
initialize();
