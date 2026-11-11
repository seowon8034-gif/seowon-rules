// 오프라인에서도 열리도록 앱 파일과 마지막으로 받은 데이터를 캐시한다.
// 항상 네트워크를 먼저 시도해 최신 버전을 쓰고, 실패하면 캐시를 쓴다.
const CACHE = 'seowon-rules-v1';
const SHELL = ['./', 'index.html', 'engine.js', 'manifest.webmanifest', 'icon.svg', 'data/sample.json',
  'https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(fetch(e.request).then(res => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy));
    }
    return res;
  }).catch(() => caches.match(e.request, { ignoreSearch: true })));
});
