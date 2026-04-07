// Cache the app shell from the worker-provided route manifest, and only
// keep same-origin GET responses that belong to that shell.
const CACHE_NAME = "safecloak-v4";
const ROUTE_MANIFEST_URL = "/routes.json";
const FALLBACK_APP_SHELL = [
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

let appShell = FALLBACK_APP_SHELL;

async function loadRouteManifest() {
  try {
    const response = await fetch(ROUTE_MANIFEST_URL, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Route manifest request failed with ${response.status}`);
    }

    const manifest = await response.json();
    if (Array.isArray(manifest.app_shell) && manifest.app_shell.length > 0) {
      appShell = manifest.app_shell;
    }
  } catch {
    appShell = FALLBACK_APP_SHELL;
  }

  return appShell;
}

function isSameOrigin(url) {
  return url.origin === self.location.origin;
}

function shouldCache(url) {
  return isSameOrigin(url) && appShell.includes(url.pathname);
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const shell = await loadRouteManifest();
      const cache = await caches.open(CACHE_NAME);
      await cache.addAll(shell);
    })()
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

  const url = new URL(event.request.url);

  if (!isSameOrigin(url)) {
    event.respondWith(fetch(event.request));
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const networkResponse = await fetch(event.request);
          if (networkResponse.ok && shouldCache(url)) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(event.request, networkResponse.clone());
          }
          return networkResponse;
        } catch {
          const cachedRoute = await caches.match(url.pathname);
          if (cachedRoute) {
            return cachedRoute;
          }
          const homePage = await caches.match("/");
          if (homePage) {
            return homePage;
          }
          return new Response("Offline and page unavailable", {
            status: 503,
            headers: { "Content-Type": "text/plain; charset=utf-8" },
          });
        }
      })()
    );
    return;
  }

  if (!shouldCache(url)) {
    event.respondWith(fetch(event.request));
    return;
  }

  event.respondWith(
    caches.match(event.request).then(async (cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }

      try {
        const networkResponse = await fetch(event.request);
        if (networkResponse.ok) {
          const cache = await caches.open(CACHE_NAME);
          cache.put(event.request, networkResponse.clone());
        }
        return networkResponse;
      } catch {
        const cachedByPath = await caches.match(url.pathname);
        if (cachedByPath) {
          return cachedByPath;
        }

        return new Response("Offline and resource unavailable", {
          status: 503,
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        });
      }
    })
  );
});
