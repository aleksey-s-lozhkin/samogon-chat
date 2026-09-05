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

self.addEventListener("push", (event) => {
    let payload = {};
    try {
        payload = event.data ? event.data.json() : {};
    } catch (_error) {
        payload = {};
    }
    event.waitUntil(self.registration.showNotification(
        payload.title || "Новое уведомление Самогона",
        {
            body: payload.body || "В Самогоне ждёт новая реплика.",
            icon: "{% static 'pwa/icon-192.png' %}",
            badge: "{% static 'pwa/icon-192.png' %}",
            tag: payload.tag || "samogon-notification",
            renotify: true,
            data: {url: payload.url || "/chat/"},
        },
    ));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const targetUrl = new URL(event.notification.data?.url || "/chat/", self.location.origin).href;
    event.waitUntil(
        clients.matchAll({type: "window", includeUncontrolled: true}).then((windows) => {
            const existing = windows.find((client) => client.url === targetUrl);
            return existing ? existing.focus() : clients.openWindow(targetUrl);
        }),
    );
});
