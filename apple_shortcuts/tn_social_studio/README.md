# Add to TN Social Studio — iPhone Shortcut

This Share Sheet Shortcut opens the selected Facebook, Instagram, TikTok, Threads, or web post in TN Social Studio's mobile capture screen. Studio creates the same rights-aware draft as the desktop browser extension without storing an API key in Shortcuts.

## Build it on iPhone

1. Open **Shortcuts**, tap **+**, and name the shortcut **Add to TN Social Studio**.
2. Open the shortcut's details, enable **Show in Share Sheet**, and limit accepted input to **URLs**, **Safari Web Pages**, and **Text**.
3. Add **Get URLs from Input**. Use **Shortcut Input** as its input.
4. Add **Get Item from List**. Set it to get the **First Item** from the URLs produced above.
5. Add **URL Encode**. Set it to encode the first URL.
6. Add **Text** and enter the following. Insert the URL Encode result after the equals sign as a Magic Variable:

   ```text
   https://studio.thetngame.com/mobile-capture/?source=
   ```

7. Add **Open URLs**, using the Text action as its input.
8. Tap **Done**.

## First test

1. Sign in to `studio.thetngame.com` in Safari once.
2. Open a public social post, tap **Share**, and choose **Add to TN Social Studio**.
3. Confirm the original post link, choose the destination account, add the creator handle and caption, and optionally choose one photo or video from Camera Roll.
4. Tap **Create Studio draft**. Studio opens the normal composer with creator-rights protection enabled.

## Expected behavior

- The Shortcut passes only the shared link. It never contains a Studio password or API key.
- A repeat capture of the same source link and destination account opens the existing draft.
- Uploaded media is saved to the Studio Media Library and attached to the draft.
- Creator rights begin as **not requested**, so the publishing guard remains active.
- If the social app does not expose a usable post URL to the iOS Share Sheet, copy the post link and run the Shortcut from that copied URL instead.

Apple requires exported Shortcut files to be signed by the Shortcuts app or by the macOS `shortcuts sign` command. After this workflow is proven on iPhone, export it from Shortcuts and share the signed iCloud link for one-tap installation.
