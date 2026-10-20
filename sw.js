/*
 * RETRO PIXEL STREAM — SERVICE WORKER (TUNDA 5 / PWA)
 * ---------------------------------------------------
 * Prinsip:
 *  - Hanya video yang PERNAH DITONTON yang masuk cache (tidak ada unduhan).
 *  - metadata.json / aliases.json: network-first (selalu segar bila online).
 *  - Segmen HLS (.ts/.m4s) & subtitle: cache-first, dibatasi ukuran (LRU).
 *  - Playlist .m3u8: network-first agar episode baru langsung terlihat.
 *  - Cache punya batas ukuran; pengaturan ada di modal "PENGATURAN".
 */

const VERSION = "20261020g";
const SHELL_CACHE = `stream-shell-${VERSION}`;
const MEDIA_CACHE = `stream-media-${VERSION}`;
const CONFIG_CACHE = "stream-config-v1";
const CONFIG_KEY = "/__stream_config__";
const DEFAULT_LIMIT_BYTES = 1024 * 1024 * 1024; // 1 GB
const MIN_LIMIT_BYTES = 200 * 1024 * 1024;
const MAX_LIMIT_BYTES = 8 * 1024 * 1024 * 1024;

const SHELL_ASSETS = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./favicon.ico",
  "./assets/css/design-system.css",
  "./assets/css/layouts/desktop.css",
  "./assets/css/layouts/mobile.css",
  "./assets/js/player.js",
  "./assets/js/explorer.js",
  "./assets/js/pwa.js",
  "./assets/js/settings.js",
  "./assets/icons/icon-192.png",
  "./assets/icons/icon-512.png",
  "./assets/icons/maskable-192.png",
  "./assets/icons/maskable-512.png",
  "./assets/icons/apple-touch-icon.png"
];

const MEDIA_HOST = "raw.githubusercontent.com";
const MEDIA_PATH_PREFIX = "/krasyid822/stream_drive/";

const MEDIA_RE = /\.(ts|m4s|mp4|m4a|aac|key)$/i;
const IMAGE_RE = /\.(png|jpe?g|webp|gif|avif|svg)$/i;
const SUBTITLE_RE = /\.vtt$/i;
const PLAYLIST_RE = /\.m3u8$/i;

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // Jangan gagalkan instalasi hanya karena 1 aset opsional tidak ada.
    await Promise.allSettled(SHELL_ASSETS.map((url) => cache.add(new Request(url, { cache: "reload" }))));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys
        .filter((key) => key !== SHELL_CACHE && key !== MEDIA_CACHE && key !== CONFIG_CACHE)
        .map((key) => caches.delete(key))
    );
    await self.clients.claim();
  })());
});

function isMediaRequest(url) {
  if (url.hostname === MEDIA_HOST && url.pathname.startsWith(MEDIA_PATH_PREFIX)) {
    return MEDIA_RE.test(url.pathname) || IMAGE_RE.test(url.pathname) || SUBTITLE_RE.test(url.pathname);
  }
  return false;
}

async function networkFirst(request, cacheKey) {
  const key = cacheKey || request;
  const cache = await caches.open(SHELL_CACHE);
  try {
    const response = await fetch(request);
    if (response && response.ok && response.type !== "opaque") {
      cache.put(key, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await cache.match(key);
    if (cached) return cached;
    throw err;
  }
}

async function playlistNetworkFirst(request) {
  const cache = await caches.open(MEDIA_CACHE);
  try {
    const response = await fetch(request);
    if (response && response.ok && response.type !== "opaque") {
      // Playlist kecil; simpan juga sebagai fallback offline.
      await cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw err;
  }
}

function withCacheHeaders(response, blobSize) {
  const headers = new Headers(response.headers);
  headers.set("x-stream-cached-at", String(Date.now()));
  headers.set("x-stream-cached-size", String(blobSize));
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers
  });
}

async function cacheFirst(request, enforce = true) {
  const cache = await caches.open(MEDIA_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response && response.ok && response.type !== "opaque" && response.status !== 206) {
    try {
      const blob = await response.clone().blob();
      const stored = withCacheHeaders(
        new Response(blob, {
          status: response.status,
          statusText: response.statusText,
          headers: response.headers
        }),
        blob.size
      );
      await cache.put(request, stored);
      if (enforce) scheduleEnforce();
    } catch (e) {
      // Jika gagal menyimpan, tetap kembalikan respons jaringan.
    }
  }
  return response;
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((response) => {
      if (response && response.ok && response.type !== "opaque") {
        cache.put(request, response.clone());
      }
      return response;
    })
    .catch(() => null);
  if (cached) {
    // Update di belakang layar
    return cached;
  }
  const fresh = await network;
  if (fresh) return fresh;
  throw new Error("Offline dan tidak ada cache");
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // Range request (video seeking) jangan di-cache; serahkan ke jaringan.
  if (request.headers.has("range")) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
    return;
  }

  if (isMediaRequest(url)) {
    if (MEDIA_RE.test(url.pathname)) {
      event.respondWith(cacheFirst(request));
    } else if (PLAYLIST_RE.test(url.pathname)) {
      event.respondWith(playlistNetworkFirst(request));
    } else {
      event.respondWith(cacheFirst(request));
    }
    return;
  }

  // Playlist lokal (bila ada)
  if (PLAYLIST_RE.test(url.pathname)) {
    event.respondWith(playlistNetworkFirst(request));
    return;
  }

  if (url.origin !== self.location.origin) return;

  if (url.pathname.endsWith("/metadata.json") || url.pathname.endsWith("/aliases.json")) {
    // Query `?t=` hanya cache-buster; simpan dengan URL kanonik agar tidak menumpuk.
    event.respondWith(networkFirst(request, url.origin + url.pathname));
    return;
  }

  if (url.pathname.startsWith("/assets/icons/")) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  if (MEDIA_RE.test(url.pathname) || IMAGE_RE.test(url.pathname) || SUBTITLE_RE.test(url.pathname)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Aset shell ringan (CSS/JS/manifest): tampilkan cache dulu, update di belakang.
  if (/\.(css|js|webmanifest|ico)$/i.test(url.pathname) || url.pathname === "/") {
    event.respondWith(staleWhileRevalidate(request));
  }
});

/* ------------------------- Pengaturan cache ------------------------- */

async function readConfig() {
  try {
    const cache = await caches.open(CONFIG_CACHE);
    const res = await cache.match(CONFIG_KEY);
    if (!res) return {};
    return await res.json();
  } catch (e) {
    return {};
  }
}

async function writeConfig(patch) {
  const current = await readConfig();
  const next = { ...current, ...patch };
  const cache = await caches.open(CONFIG_CACHE);
  await cache.put(
    CONFIG_KEY,
    new Response(JSON.stringify(next), { headers: { "content-type": "application/json" } })
  );
  return next;
}

async function getLimit() {
  const cfg = await readConfig();
  const limit = Number(cfg.limitBytes);
  if (!Number.isFinite(limit)) return DEFAULT_LIMIT_BYTES;
  return Math.max(MIN_LIMIT_BYTES, Math.min(MAX_LIMIT_BYTES, limit));
}

const LIMITS = {
  min: MIN_LIMIT_BYTES,
  max: MAX_LIMIT_BYTES,
  default: DEFAULT_LIMIT_BYTES
};

let lastEnforce = 0;
let enforcing = false;

function scheduleEnforce() {
  const now = Date.now();
  if (enforcing || now - lastEnforce < 8000) return;
  lastEnforce = now;
  enforcing = true;
  enforceLimit()
    .catch(() => {})
    .finally(() => { enforcing = false; });
}

async function enforceLimit() {
  if (!self.navigator || !navigator.storage || !navigator.storage.estimate) return;
  const limit = await getLimit();
  const estimate = await navigator.storage.estimate();
  let usage = estimate.usage || 0;
  if (usage <= limit) return;

  const cache = await caches.open(MEDIA_CACHE);
  const keys = await cache.keys();
  const entries = [];
  for (const request of keys) {
    const res = await cache.match(request);
    if (!res) continue;
    const at = Number(res.headers.get("x-stream-cached-at") || 0);
    const size = Number(res.headers.get("x-stream-cached-size") || 0);
    entries.push({ request, at, size });
  }
  entries.sort((a, b) => a.at - b.at);

  // Buang yang paling lama sampai pemakaian turun ke ~80% batas.
  const target = Math.max(0, Math.floor(limit * 0.8));
  for (const entry of entries) {
    if (usage <= target) break;
    await cache.delete(entry.request);
    usage -= entry.size || 0;
  }
}

async function cacheInfo() {
  const media = await caches.open(MEDIA_CACHE);
  const shell = await caches.open(SHELL_CACHE);
  const mediaKeys = await media.keys();
  const shellKeys = await shell.keys();
  let mediaBytes = 0;
  for (const request of mediaKeys) {
    const res = await media.match(request);
    if (!res) continue;
    mediaBytes += Number(res.headers.get("x-stream-cached-size") || 0);
  }
  let estimate = { usage: 0, quota: 0 };
  if (navigator.storage && navigator.storage.estimate) {
    estimate = await navigator.storage.estimate();
  }
  let persisted = false;
  if (navigator.storage && navigator.storage.persisted) {
    persisted = await navigator.storage.persisted();
  }
  return {
    version: VERSION,
    usage: estimate.usage || 0,
    quota: estimate.quota || 0,
    mediaBytes,
    mediaEntries: mediaKeys.length,
    shellEntries: shellKeys.length,
    limitBytes: await getLimit(),
    limits: LIMITS,
    persisted
  };
}

async function clearMedia() {
  await caches.delete(MEDIA_CACHE);
  await caches.open(MEDIA_CACHE);
  return cacheInfo();
}

self.addEventListener("message", (event) => {
  const data = event.data || {};
  const port = event.ports && event.ports[0];
  const reply = (payload) => {
    if (port) port.postMessage(payload);
  };

  if (data.type === "GET_CACHE_INFO") {
    event.waitUntil(cacheInfo().then(reply).catch((e) => reply({ error: String(e) })));
    return;
  }

  if (data.type === "CLEAR_MEDIA") {
    event.waitUntil(clearMedia().then(reply).catch((e) => reply({ error: String(e) })));
    return;
  }

  if (data.type === "SET_CACHE_LIMIT") {
    const bytes = Number(data.bytes);
    const clamped = Number.isFinite(bytes)
      ? Math.max(MIN_LIMIT_BYTES, Math.min(MAX_LIMIT_BYTES, bytes))
      : DEFAULT_LIMIT_BYTES;
    event.waitUntil(
      writeConfig({ limitBytes: clamped })
        .then(() => enforceLimit())
        .then(cacheInfo)
        .then(reply)
        .catch((e) => reply({ error: String(e) }))
    );
    return;
  }

  if (data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});
