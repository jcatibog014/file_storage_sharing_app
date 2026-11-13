// J: Drive's service worker. It makes the app installable on a phone and keeps
// the look (styles, scripts, icons) ready even on a slow connection.
// Pages always come from the network, so files are never out of date;
// without a connection a small "You're offline" page shows instead.
"use strict";

const CACHE = "jdrive-v1"; // change the number when these files change
const FILES = [
  "/static/style.css",
  "/static/app.js",
  "/static/theme.js",
  "/static/fonts/inter-latin-400-normal.woff2",
  "/static/fonts/plus-jakarta-sans-700.woff2",
  "/static/icons/icon-192.png",
  "/static/offline.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Throw away the caches of older versions.
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    // A page: always the network; the offline page only when there's none.
    event.respondWith(fetch(request).catch(() => caches.match("/static/offline.html")));
    return;
  }
  if (FILES.includes(new URL(request.url).pathname)) {
    // Our own styles/scripts/icons: the network first (so updates show), the cache as a backup.
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request)),
    );
  }
});
