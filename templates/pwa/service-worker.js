{% load static %}

const CACHE_NAME = "samogon-public-v2";
const APP_SHELL = [
    "/offline/",
    "{% static 'pwa/icon-192.png' %}",
    "{% static 'pwa/icon-512.png' %}",
    "{% static 'chat/images/favicon.svg' %}",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(APP_SHELL))
            .then(() => self.skipWaiting()),
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((names) => Promise.all(
            names
                .filter((name) => name.startsWith("samogon-") && name !== CACHE_NAME)
                .map((name) => caches.delete(name)),
        )).then(() => self.clients.claim()),
    );
});

self.addEventListener("fetch", (event) => {
    const { request } = event;
    const url = new URL(request.url);

    if (request.method !== "GET" || url.origin !== self.location.origin) {
        return;
    }

    if (url.pathname.startsWith("/static/")) {
        event.respondWith(
            // Свежий код важнее офлайн-кеша: иначе UI может отстать от сервера.
            fetch(request).then((response) => {
                const copy = response.clone();
                caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
                return response;
            }).catch(() => caches.match(request)),
        );
        return;
    }

    if (request.mode === "navigate") {
        event.respondWith(
            fetch(request).catch(() => caches.match("/offline/")),
        );
    }
});
