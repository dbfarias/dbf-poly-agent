/* Service Worker for PolyBot push notifications. */

self.addEventListener("push", (event) => {
  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch {
    payload = { title: "PolyBot", body: event.data.text() };
  }

  const options = {
    body: payload.body || "",
    icon: "/polybot-icon-192.png",
    badge: "/polybot-badge-72.png",
    vibrate: [100, 50, 100],
    tag: payload.tag || "polybot",
    data: payload.data || { url: "/" },
    renotify: true,
  };

  event.waitUntil(self.registration.showNotification(payload.title || "PolyBot", options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const url = event.notification.data?.url || "/";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      // Focus existing tab if available
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && "focus" in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      // Open new window
      return clients.openWindow(url);
    }),
  );
});
