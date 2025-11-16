const CACHE_NAME = 'archives-cache-v1';
const urlsToCache = [
    '/',
    '/static/manifest.json',
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(urlsToCache))
    );
});

self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    // never cache page-image requests or regenerate attempts
    if (url.pathname.startsWith('/documents/') && url.pathname.includes('/page/')) {
        // always fetch from network so we can regenerate when needed
        event.respondWith(fetch(event.request));
        return;
    }

    // Telegram widget proxies should always hit the network
    if (url.pathname.startsWith('/internal/api/telegram-') || url.pathname.startsWith('/api/telegram-messages')) {
        event.respondWith(fetch(event.request));
        return;
    }

    event.respondWith(
        caches.match(event.request).then(response => {
            return response || fetch(event.request);
        })
    );
});
