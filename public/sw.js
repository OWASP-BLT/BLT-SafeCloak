const CACHE_NAME = "safecloak-v3";

const APP_SHELL = [
  "/",
  "/video-chat",
  "/notes",
  "/consent",
  "/css/main.css",
  "/js/ui.js",
  "/js/crypto.js",
  "/js/notes.js",
  "/js/consent.js",
  "/js/video.js",
  "/img/logo.png",
  "/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") {
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const networkResponse = await fetch(event.request);
          const cache = await caches.open(CACHE_NAME);
          cache.put(event.request, networkResponse.clone());
          return networkResponse;
        } catch {
          const url = new URL(event.request.url);
          const cachedRoute = await caches.match(url.pathname);
          if (cachedRoute) {
            return cachedRoute;
          }
          return caches.match("/");
        }
      })()
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(async (cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }

      try {
        const networkResponse = await fetch(event.request);
        const url = new URL(event.request.url);
        if (url.origin === self.location.origin && networkResponse.ok) {
          const cache = await caches.open(CACHE_NAME);
          cache.put(event.request, networkResponse.clone());
        }
        return networkResponse;
      } catch {
        const url = new URL(event.request.url);
        return caches.match(url.pathname);
      }
    })
  );
});
