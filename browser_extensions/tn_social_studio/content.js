(function initializeCaptureScript() {
  if (globalThis.__tnSocialStudioCaptureInitialized) return;
  globalThis.__tnSocialStudioCaptureInitialized = true;

  const PLATFORM_HOSTS = [
    ["instagram.com", "instagram"],
    ["facebook.com", "facebook"],
    ["fb.watch", "facebook"],
    ["tiktok.com", "tiktok"],
    ["threads.net", "threads"],
  ];

  function text(value, maxLength = 10000) {
    return String(value || "").replace(/\u00a0/g, " ").replace(/[ \t]+\n/g, "\n").trim().slice(0, maxLength);
  }

  function meta(...selectors) {
    for (const selector of selectors) {
      const value = document.querySelector(selector)?.content;
      if (value) return text(value);
    }
    return "";
  }

  function platformFor(hostname) {
    const host = hostname.toLowerCase().replace(/^www\./, "");
    for (const [suffix, platform] of PLATFORM_HOSTS) {
      if (host === suffix || host.endsWith(`.${suffix}`)) return platform;
    }
    return "website";
  }

  function canonicalUrl() {
    const candidate = document.querySelector('link[rel="canonical"]')?.href || location.href;
    try {
      const url = new URL(candidate, location.href);
      url.hash = "";
      for (const key of [...url.searchParams.keys()]) {
        if (key.startsWith("utm_") || ["fbclid", "igshid", "share_id"].includes(key)) {
          url.searchParams.delete(key);
        }
      }
      return url.href;
    } catch (_error) {
      return location.href.split("#")[0];
    }
  }

  function externalId(url, platform) {
    const path = url.pathname;
    if (platform === "instagram") return path.match(/\/(?:p|reel|tv)\/([^/?#]+)/i)?.[1] || "";
    if (platform === "tiktok") return path.match(/\/video\/(\d+)/i)?.[1] || "";
    if (platform === "facebook") {
      return (
        url.searchParams.get("story_fbid") ||
        url.searchParams.get("fbid") ||
        url.searchParams.get("v") ||
        path.match(/\/(?:posts|videos|reel)\/([^/?#]+)/i)?.[1] ||
        ""
      );
    }
    return "";
  }

  function likelyPostRoot() {
    const candidates = [...document.querySelectorAll("article")].filter((node) => {
      const rect = node.getBoundingClientRect();
      return rect.width > 250 && rect.height > 150 && rect.bottom > 0 && rect.top < innerHeight;
    });
    return candidates[0] || document.querySelector("main") || document.body;
  }

  function visibleCaption(root, platform) {
    const preferredSelectors = {
      facebook: ['[data-ad-comet-preview="message"]'],
      instagram: ["h1"],
      tiktok: ['[data-e2e="browse-video-desc"]', '[data-e2e="video-desc"]'],
    };
    for (const selector of preferredSelectors[platform] || []) {
      const preferred = text(root.querySelector(selector)?.innerText);
      if (preferred) return preferred;
    }
    let candidate = "";
    if (!candidate) {
      const blocks = [...root.querySelectorAll('div[dir="auto"], span[dir="auto"], h1, p')]
        .map((node) => text(node.innerText))
        .filter((value) => value.length >= 20 && value.length <= 10000);
      candidate = blocks.sort((a, b) => b.length - a.length)[0] || "";
    }
    return text(candidate);
  }

  function normalizeSocialDescription(value, platform) {
    const description = text(value);
    if (platform !== "instagram") return description;
    const quoted = description.match(/:\s*[“\"]([\s\S]+)[”\"]\s*$/);
    return text(quoted?.[1] || description);
  }

  function creatorDetails(platform, titleValue, descriptionValue) {
    const combined = `${titleValue}\n${descriptionValue}`;
    const patterns = {
      instagram: [
        /([A-Za-z0-9._]+)\s+on Instagram/i,
        /\(@([A-Za-z0-9._]+)\)/,
        /@([A-Za-z0-9._]+)/,
      ],
      tiktok: [/([A-Za-z0-9._-]+)\s+on TikTok/i, /@([A-Za-z0-9._-]+)/],
      threads: [/@([A-Za-z0-9._]+)/],
    };
    for (const pattern of patterns[platform] || []) {
      const match = combined.match(pattern);
      if (match?.[1]) return { handle: match[1], name: "" };
    }
    const author = meta('meta[name="author"]');
    return { handle: "", name: author };
  }

  function mediaCandidates(root) {
    const videos = [];
    const images = [];
    const push = (url, type, width = 0, height = 0) => {
      if (!url || /^(data|blob):/i.test(url)) return;
      let absolute;
      try {
        const parsed = new URL(url, location.href);
        if (!/^https?:$/.test(parsed.protocol)) return;
        absolute = parsed.href;
      } catch (_error) {
        return;
      }
      const target = type === "video" ? videos : images;
      if (target.some((item) => item.url === absolute)) return;
      target.push({ url: absolute, type, width, height });
    };

    const ogVideo = meta('meta[property="og:video:secure_url"]', 'meta[property="og:video"]');
    push(ogVideo, "video");
    if (!ogVideo) {
      push(
        meta('meta[property="og:image:secure_url"]', 'meta[property="og:image"]', 'meta[name="twitter:image"]'),
        "image",
      );
    }

    for (const video of root.querySelectorAll("video")) {
      push(video.currentSrc || video.src, "video", video.videoWidth, video.videoHeight);
      if (!video.currentSrc && !video.src) push(video.poster, "image", video.videoWidth, video.videoHeight);
    }
    for (const image of root.querySelectorAll("img")) {
      const width = image.naturalWidth || image.width;
      const height = image.naturalHeight || image.height;
      if (width >= 300 && height >= 250) push(image.currentSrc || image.src, "image", width, height);
    }
    return (videos.length ? videos : images).slice(0, 10);
  }

  function capture() {
    const source = new URL(canonicalUrl());
    const platform = platformFor(source.hostname);
    const root = likelyPostRoot();
    const pageTitle = meta('meta[property="og:title"]', 'meta[name="twitter:title"]') || document.title;
    const socialDescription = normalizeSocialDescription(
      meta('meta[property="og:description"]', 'meta[name="twitter:description"]', 'meta[name="description"]'),
      platform,
    );
    const visible = visibleCaption(root, platform);
    const caption = text(
      maybeCaptionFromSelection() ||
        (platform === "instagram" ? socialDescription || visible : visible || socialDescription) ||
        maybeCaptionFromJsonLd(),
    );
    const creator = creatorDetails(platform, pageTitle, `${caption}\n${meta('meta[property="og:description"]')}`);
    return {
      sourceUrl: source.href,
      sourcePlatform: platform,
      sourceExternalId: externalId(source, platform),
      creatorHandle: creator.handle,
      creatorName: creator.name,
      title: text(pageTitle.replace(/\s*[|•-]\s*(Instagram|Facebook|TikTok|Threads).*$/i, ""), 255),
      caption,
      media: mediaCandidates(root),
    };
  }

  function maybeCaptionFromSelection() {
    return globalThis.getSelection?.()?.toString() || "";
  }

  function maybeCaptionFromJsonLd() {
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const value = JSON.parse(script.textContent || "null");
        const entries = Array.isArray(value) ? value : [value];
        for (const entry of entries) {
          if (entry?.articleBody || entry?.description) return entry.articleBody || entry.description;
        }
      } catch (_error) {
        // Ignore malformed third-party metadata.
      }
    }
    return "";
  }

  const extensionApi = globalThis.browser || globalThis.chrome;
  extensionApi.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "tn-social-studio:capture") return undefined;
    try {
      const result = capture();
      sendResponse({ ok: true, result });
    } catch (error) {
      sendResponse({ ok: false, error: error?.message || "Could not read this page." });
    }
    return true;
  });
})();
