/* No offline caching: account and forecast responses must remain private and fresh. */
self.addEventListener("push", event => {
    let data;
    try { data = event.data.json(); } catch { return; }
    event.waitUntil(self.registration.showNotification(data.title || "NoRain", {
        body: data.body, tag: data.tag, data: { url: data.url }, icon: "/favicon.ico",
    }));
});
self.addEventListener("notificationclick", event => {
    event.notification.close();
    const url = new URL(event.notification.data?.url || "/account", self.location.origin);
    if (url.origin !== self.location.origin) return;
    event.waitUntil(self.clients.openWindow(url.href));
});
