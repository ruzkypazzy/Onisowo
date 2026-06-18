// Minimal service worker for Àkànjí PWA.
// Caches static assets for offline shell + faster cold start.

const CACHE = "akanji-v1";
const SHELL = ["/", "/index.html", "/app.css", "/app.js", "/manifest.json",
  "/assets/profile_picture.png", "/assets/brand_mark.png",
  "/assets/background_market_scene.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Network-first for /chat API
  if (url.pathname.startsWith("/chat") || url.pathname.startsWith("/health")) {
    e.respondWith(fetch(e.request).catch(() => new Response(JSON.stringify({error: "offline"}), {status: 503})));
    return;
  }
  // Cache-first for shell + assets
  e.respondWith(
    caches.match(e.request).then((cached) =>
      cached || fetch(e.request).then((resp) => {
        if (resp.ok && (e.request.method === "GET")) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return resp;
      })
    )
  );
});
