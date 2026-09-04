const extensionApi = globalThis.browser || globalThis.chrome;

extensionApi.runtime.onInstalled.addListener(async () => {
  try {
    await extensionApi.contextMenus.removeAll();
    extensionApi.contextMenus.create({
      id: "add-to-tn-social-studio",
      title: "Add to TN Social Studio",
      contexts: ["page", "link", "image", "video"],
    });
  } catch (_error) {
    // The toolbar action remains available if a browser blocks context menus.
  }
});

extensionApi.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "add-to-tn-social-studio" || !tab?.id) return;
  try {
    await extensionApi.action.openPopup();
  } catch (_error) {
    const popupUrl = extensionApi.runtime.getURL(`popup.html?tabId=${tab.id}`);
    await extensionApi.tabs.create({ url: popupUrl });
  }
});
