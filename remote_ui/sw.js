/* 小四控制台 Service Worker：静态壳缓存，API 永不缓存 */
const CACHE = 'maid-shell-v5';
const SHELL = [
  '/',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/avatar.png'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c =>
    Promise.allSettled(SHELL.map(u => c.add(u)))
  ).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/')) return; // API 直连，绝不缓存
  if (e.request.method !== 'GET') return;

  // 页面：只走网络（服务器带 no-store，永远最新），不回退缓存，杜绝旧界面
  if (e.request.mode === 'navigate' || url.pathname === '/') {
    e.respondWith(fetch(e.request));
    return;
  }
  // 其他静态资源：缓存优先
  e.respondWith(
    caches.match(e.request).then(hit => hit || fetch(e.request).then(r => {
      if (r.ok && url.origin === location.origin) {
        const cp = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, cp));
      }
      return r;
    }))
  );
});
