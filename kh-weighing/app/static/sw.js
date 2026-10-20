// Service Worker: оболочка приложения открывается без интернета.
// Запросы к /api не кэшируются — данные идут через офлайн-очередь (outbox.js).
const VERSION = "kh-weighing-v1";
const SHELL = [
  "/", "/index.html", "/styles.css", "/app.js", "/i18n.js", "/outbox.js",
  "/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  // Сеть в приоритете (чтобы обновления доходили сразу), при её отсутствии — кэш.
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(async () => {
        const cached = await caches.match(req, { ignoreSearch: true });
        if (cached) return cached;
        if (req.mode === "navigate") return caches.match("/index.html");
        return Response.error();
      })
  );
});
