// Auto DM — service worker (Fase 53).
//
// Estratégia:
//   * app shell (HTML/CSS/JS/ícones) é pré-cacheado no install → segunda
//     abertura carrega sem rede;
//   * navegações usam network-first com fallback pro shell em cache (o app
//     abre offline e mostra o erro de rede pela própria UI);
//   * assets estáticos usam cache-first com revalidação em segundo plano;
//   * nada de `/api/` passa pelo cache — estado de jogo é sempre da rede.
//
// Ao mexer em qualquer arquivo do shell: bumpe `CACHE_VERSION` e mantenha
// as querystrings `?v=` iguais às de `index.html` (há teste checando isso).

const CACHE_VERSION = "auto-dm-v71";

const PRECACHE_URLS = [
  "/",
  "/css/tokens.css?v=65",
  "/css/base.css?v=65",
  "/css/components.css?v=65",
  "/css/utilities.css?v=65",
  "/style.css?v=65",
  "/css/shell.css?v=65",
  "/css/lobby.css?v=65",
  "/css/landing.css?v=71",
  "/css/wizard.css?v=65",
  "/css/game.css?v=68",
  "/css/admin.css?v=66",
  "/app.js?v=69",
  "/shell.js?v=63",
  "/pwa.js?v=70",
  "/manifest.webmanifest",
  "/assets/icons/lucide.svg",
  "/assets/icons/d20.svg",
  "/assets/icons/icon-192.png",
  "/assets/icons/icon-512.png",
  "/assets/icons/apple-touch-icon.png",
  "/assets/icons/favicon-32.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_VERSION);
      // `reload` evita gravar no cache algo que o HTTP cache já tinha velho.
      await cache.addAll(PRECACHE_URLS.map((url) => new Request(url, { cache: "reload" })));
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((name) => name !== CACHE_VERSION).map((name) => caches.delete(name)),
      );
      if (self.registration.navigationPreload) {
        await self.registration.navigationPreload.enable();
      }
      await self.clients.claim();
    })(),
  );
});

function isCacheable(request, url) {
  if (request.method !== "GET") return false;
  if (url.origin !== self.location.origin) return false;
  if (url.pathname.startsWith("/api/")) return false;
  if (url.pathname === "/design-system") return false;
  return true;
}

async function handleNavigation(event) {
  const cache = await caches.open(CACHE_VERSION);
  try {
    const preloaded = await event.preloadResponse;
    const response = preloaded || (await fetch(event.request));
    if (response && response.ok) {
      cache.put("/", response.clone());
    }
    return response;
  } catch (error) {
    const cached = (await cache.match("/")) || (await cache.match("/index.html"));
    if (cached) return cached;
    throw error;
  }
}

async function handleAsset(request) {
  const cache = await caches.open(CACHE_VERSION);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((response) => {
      if (response && response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => undefined);

  // Com cópia local respondemos na hora e deixamos a revalidação correr solta.
  if (cached) return cached;

  const response = await network;
  if (response) return response;
  throw new Error("Recurso indisponível offline");
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (!isCacheable(event.request, url)) return;

  if (event.request.mode === "navigate") {
    event.respondWith(handleNavigation(event));
    return;
  }
  event.respondWith(handleAsset(event.request));
});

self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting") self.skipWaiting();
});
