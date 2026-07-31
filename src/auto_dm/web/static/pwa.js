// Auto DM — registro do service worker e do prompt de instalação (Fase 53).

const INSTALL_DISMISSED_KEY = "autodm.install.dismissed";

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  // O registro só depois do load pra não competir com o primeiro render.
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((error) => {
      console.warn("[pwa] service worker não registrado:", error);
    });
  });

  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    // Versão nova assumiu o controle: recarrega uma única vez.
    if (reloading) return;
    reloading = true;
    window.location.reload();
  });
}

function setupInstallPrompt() {
  // Um botão na landing e outro no menu da conta — o navegador só dispara
  // `beforeinstallprompt` quando o app é de fato instalável.
  const buttons = [...document.querySelectorAll("[data-install-app]")];
  if (buttons.length === 0) return;

  let deferredPrompt = null;
  const setVisible = (visible) => {
    buttons.forEach((button) => {
      button.hidden = !visible;
    });
  };

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    if (localStorage.getItem(INSTALL_DISMISSED_KEY) === "1") return;
    setVisible(true);
  });

  buttons.forEach((button) => {
    button.addEventListener("click", async () => {
      if (!deferredPrompt) return;
      setVisible(false);
      deferredPrompt.prompt();
      const { outcome } = await deferredPrompt.userChoice;
      deferredPrompt = null;
      if (outcome === "dismissed") localStorage.setItem(INSTALL_DISMISSED_KEY, "1");
    });
  });

  window.addEventListener("appinstalled", () => {
    setVisible(false);
    deferredPrompt = null;
  });
}

registerServiceWorker();
setupInstallPrompt();
