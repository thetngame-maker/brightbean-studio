# Add to TN Social Studio browser extension

This Manifest V3 WebExtension captures the current social post as an editable, rights-aware draft in TN Social Studio. The same source works in Chrome and can be converted into a Safari extension with Apple's standard Xcode converter.

## What it captures

- Original post URL and platform
- Creator handle/name when the page exposes it
- Caption and title, editable before saving
- Up to 10 detected photos or videos when the host permits downloads
- A target Studio social account

Captured community content enters Studio as a pending UGC submission with a `not_requested` rights passport. It is linked to the composer draft through `ContentPerformanceProfile`, so Studio's existing publishing guard requires creator permission and credit review before scheduling.

## Studio setup

1. Open **Organization → API Keys** in Studio.
2. Create a key named **Browser extension**.
3. Grant **Create posts** and **Upload media**.
4. Allowlist every social account that should appear in the extension.
5. Copy the key immediately; Studio only displays it once.

The key stays in the browser's extension-local storage. Revoke it in Studio if the browser is lost or shared.

## Install in Chrome

1. Open `chrome://extensions`.
2. Turn on **Developer mode**.
3. Choose **Load unpacked**.
4. Select this `browser_extensions/tn_social_studio` directory.
5. Pin **Add to TN Social Studio** to the toolbar.
6. Open the extension's **Options**, paste the Studio API key, and test the connection.

## Install in Safari

Safari packages WebExtensions inside a small native Xcode app. On a Mac with Xcode installed, run:

```bash
xcrun safari-web-extension-converter browser_extensions/tn_social_studio \
  --project-location ./build/safari \
  --app-name "TN Social Studio"
```

Open the generated Xcode project, select a development team, and run the macOS app. Then enable **TN Social Studio** under **Safari → Settings → Extensions**. For personal use, Xcode may periodically require the app to be rebuilt unless it is distributed and signed through the Apple Developer Program.

## Use

1. Open the individual Facebook post, Instagram post/Reel, TikTok, or public web page—not the scrolling feed.
2. Click the toolbar button or right-click and choose **Add to TN Social Studio**.
3. Review the detected creator, caption, account, and media.
4. Click **Add to TN Social Studio**, then **Open draft in Studio**.

Social networks sometimes expose a low-resolution preview or block direct media downloads. The extension still creates the caption/source draft and tells the user when media must be added manually.

## Development notes

- The production Studio origin is the only remote origin granted by default.
- Media/CDN access is requested only after the user chooses to include detected media.
- A custom HTTPS Studio origin is optional and requested from the browser when configured.
- Re-saving the same source URL for the same account returns the existing draft instead of creating a duplicate.
