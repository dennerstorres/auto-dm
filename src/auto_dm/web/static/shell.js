const PAGE_META = {
  "auth-screen": {
    label: "Início",
    title: "Auto DM | Sua aventura, suas escolhas",
  },
  "lobby-screen": {
    label: "Campanhas",
    title: "Campanhas | Auto DM",
  },
  "wizard-screen": {
    label: "Criar personagem",
    title: "Criar personagem | Auto DM",
  },
  "game-screen": {
    label: "Mesa de jogo",
    title: "Mesa de jogo | Auto DM",
  },
  "admin-panel-screen": {
    label: "Administração",
    title: "Administração | Auto DM",
  },
};

const dialogState = new WeakMap();
let pendingRequests = 0;
let loadingTimer = null;
let toastSequence = 0;
let confirmationResolver = null;

const byId = (id) => document.getElementById(id);

function focusableElements(root) {
  return [...root.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
    'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )].filter((element) => !element.hidden && element.getClientRects().length > 0);
}

function visibleDialog() {
  const containers = [...document.querySelectorAll(
    ".modal:not([hidden]), #auth-dialog:not([hidden])",
  )];
  const container = containers.at(-1);
  return container ? container.querySelector('[role="dialog"]') : null;
}

function syncScrollLock() {
  document.body.classList.toggle("dialog-open", visibleDialog() !== null);
}

export function openDialog(containerOrId, options = {}) {
  const container = typeof containerOrId === "string" ? byId(containerOrId) : containerOrId;
  if (!container) return;
  if (!dialogState.has(container)) {
    dialogState.set(container, { returnFocus: document.activeElement });
  }
  container.hidden = false;
  container.style.removeProperty("display");
  container.classList.add("is-open");
  syncScrollLock();

  requestAnimationFrame(() => {
    const dialog = container.querySelector('[role="dialog"]');
    if (!dialog) return;
    const preferred = options.initialFocus
      ? dialog.querySelector(options.initialFocus)
      : null;
    const target = preferred || focusableElements(dialog)[0] || dialog;
    target.focus({ preventScroll: true });
  });
}

export function closeDialog(containerOrId, options = {}) {
  const container = typeof containerOrId === "string" ? byId(containerOrId) : containerOrId;
  if (!container || container.hidden) return;
  const state = dialogState.get(container);
  container.hidden = true;
  container.classList.remove("is-open");
  dialogState.delete(container);
  syncScrollLock();

  if (options.restoreFocus !== false && state?.returnFocus?.isConnected) {
    state.returnFocus.focus({ preventScroll: true });
  }
}

function requestDialogClose(container) {
  const close = container.querySelector("[data-dialog-close]");
  if (close) close.click();
}

function handleDialogKeydown(event) {
  const dialog = visibleDialog();
  if (!dialog) return;
  const container = dialog.closest(".modal, #auth-dialog");

  if (event.key === "Escape") {
    event.preventDefault();
    requestDialogClose(container);
    return;
  }
  if (event.key !== "Tab") return;

  const focusable = focusableElements(dialog);
  if (focusable.length === 0) {
    event.preventDefault();
    dialog.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function handleBackdropClick(event) {
  const container = event.target.closest(".modal, #auth-dialog");
  if (!container || container.hidden) return;
  if (event.target === container || event.target.hasAttribute("data-dialog-backdrop")) {
    requestDialogClose(container);
  }
}

export function initDialogSystem() {
  document.addEventListener("keydown", handleDialogKeydown);
  document.addEventListener("click", handleBackdropClick);
}

export function updateShell({ screenId, user, hasSession = false }) {
  const authenticated = screenId !== "auth-screen" && Boolean(user);
  const meta = PAGE_META[screenId] || PAGE_META["lobby-screen"];
  const navigation = byId("app-navigation");
  const pageTitle = byId("shell-page-title");
  const lobby = byId("shell-lobby-btn");
  const game = byId("shell-game-btn");
  const admin = byId("shell-admin-btn");

  if (navigation) navigation.hidden = !authenticated;
  if (pageTitle) {
    pageTitle.textContent = meta.label;
    pageTitle.hidden = !authenticated;
  }
  document.title = meta.title;
  document.body.dataset.screen = screenId;

  if (lobby) lobby.hidden = !authenticated || screenId === "lobby-screen";
  if (game) game.hidden = !authenticated || !hasSession || screenId === "game-screen";
  if (admin) admin.hidden = !authenticated || user?.role !== "admin" || screenId === "admin-panel-screen";

  for (const button of document.querySelectorAll("[data-shell-screen]")) {
    const active = button.dataset.shellScreen === screenId;
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
}

export function setShellUser(user) {
  const who = byId("who");
  const role = byId("user-role");
  if (who) who.textContent = user ? `@${user.username}` : "";
  if (role) {
    role.textContent = user?.role === "admin" ? "Admin" : "";
    role.hidden = user?.role !== "admin";
  }
}

export function updateConnectivity() {
  const offline = !navigator.onLine;
  const banner = byId("offline-banner");
  if (banner) banner.hidden = !offline;
  document.body.classList.toggle("is-offline", offline);
}

export function beginRequest() {
  pendingRequests += 1;
  if (pendingRequests !== 1) return;
  clearTimeout(loadingTimer);
  loadingTimer = setTimeout(() => {
    const status = byId("global-loading");
    const main = byId("main-content");
    if (status) status.hidden = false;
    if (main) main.setAttribute("aria-busy", "true");
  }, 150);
}

export function endRequest() {
  pendingRequests = Math.max(0, pendingRequests - 1);
  if (pendingRequests > 0) return;
  clearTimeout(loadingTimer);
  const status = byId("global-loading");
  const main = byId("main-content");
  if (status) status.hidden = true;
  if (main) main.removeAttribute("aria-busy");
}

export function showToast(message, kind = "info", timeout = 4500) {
  const region = byId("toast-region");
  if (!region || !message) return;
  const toast = document.createElement("div");
  const id = `toast-${++toastSequence}`;
  toast.id = id;
  toast.className = `toast toast-${kind}`;
  toast.setAttribute("role", kind === "error" ? "alert" : "status");

  const text = document.createElement("span");
  text.textContent = message;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-close";
  close.setAttribute("aria-label", "Fechar notificação");
  const closeIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  closeIcon.setAttribute("class", "icon icon-compact");
  closeIcon.setAttribute("aria-hidden", "true");
  const closeIconUse = document.createElementNS("http://www.w3.org/2000/svg", "use");
  closeIconUse.setAttribute("href", "/assets/icons/lucide.svg#x");
  closeIcon.appendChild(closeIconUse);
  close.appendChild(closeIcon);
  close.onclick = () => toast.remove();
  toast.append(text, close);
  region.appendChild(toast);

  if (timeout > 0) window.setTimeout(() => toast.remove(), timeout);
}

function finishConfirmation(confirmed) {
  const resolver = confirmationResolver;
  confirmationResolver = null;
  closeDialog("confirm-modal");
  if (resolver) resolver(confirmed);
}

export function confirmAction(message, options = {}) {
  const modal = byId("confirm-modal");
  if (!modal) return Promise.resolve(false);
  if (confirmationResolver) finishConfirmation(false);

  byId("confirm-title").textContent = options.title || "Confirmar ação";
  byId("confirm-message").textContent = message;
  const confirm = byId("confirm-accept");
  confirm.textContent = options.confirmLabel || "Confirmar";
  confirm.classList.toggle("button-danger", options.danger !== false);
  openDialog(modal, { initialFocus: "#confirm-cancel" });

  return new Promise((resolve) => {
    confirmationResolver = resolve;
  });
}

export function initShell() {
  initDialogSystem();
  updateConnectivity();
  window.addEventListener("online", updateConnectivity);
  window.addEventListener("offline", updateConnectivity);
  byId("confirm-cancel")?.addEventListener("click", () => finishConfirmation(false));
  byId("confirm-accept")?.addEventListener("click", () => finishConfirmation(true));
}
