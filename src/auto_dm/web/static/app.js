import {
  beginRequest,
  closeDialog,
  confirmAction,
  endRequest,
  initShell,
  openDialog,
  setShellUser,
  showToast,
  updateShell,
} from "./shell.js?v=63";

// Auto DM — console UI client (Phase 26).
//
// Pure-vanilla: stores the JWT in localStorage, sends it as
// `Authorization: Bearer <token>`, and renders the game log.
//
// API endpoints (backend on /api):
//   POST   /api/auth/signup         {username, password}  -> {token, user}
//   POST   /api/auth/login          {username, password}  -> {token, user}
//   GET    /api/auth/me                                     -> {user}
//   GET    /api/saves                                      -> [SaveOut]
//   POST   /api/saves                {slug, state}         -> SaveOut
//   DELETE /api/saves/{slug}                               -> 204
//   POST   /api/saves/{slug}/load                          -> {session_id, state}
//   GET    /api/sessions                                   -> {session_ids}
//   POST   /api/sessions              {state}              -> {session_id, state}
//   GET    /api/sessions/{sid}                             -> {state}
//   POST   /api/sessions/{sid}/input  {line}               -> {result, state}
//   DELETE /api/sessions/{sid}                             -> 204

const API_BASE = ""; // same origin (Vercel would be cross-origin, but
                    // for the dev server, same origin works)

// Shown when the user hits their daily quota (429 from /input).
const LIMIT_REACHED_MSG =
  "Limite diário atingido. Volte amanhã ou peça ao administrador " +
  "para aumentar sua cota.";

// --- Auth state ---
const TOKEN_KEY = "auto_dm_token";
const USER_KEY = "auto_dm_user";

function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

function getUser() {
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

function setUser(u) {
  if (u) localStorage.setItem(USER_KEY, JSON.stringify(u));
  else localStorage.removeItem(USER_KEY);
}

// True when the logged-in user has the `admin` role. Drives the lobby
// (cross-user save list + delete buttons), the read-only game view, and
// the admin-only "Criar jogo vazio" advanced option.
function isAdmin() {
  const u = getUser();
  return !!(u && u.role === "admin");
}

// --- Fetch wrapper ---
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const tok = getToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  if (opts.headers) Object.assign(headers, opts.headers);
  beginRequest();
  try {
    const res = await fetch(API_BASE + path, {
      method: opts.method || "GET",
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!res.ok) {
      let detail = res.statusText;
      let payload = null;
      try {
        payload = await res.json();
        // FastAPI HTTPException detail may be a dict (429 quota) or string.
        detail = payload.detail || JSON.stringify(payload);
      } catch (_) {}
      const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      err.status = res.status;
      err.payload = payload;
      throw err;
    }
    if (res.status === 204) return null;
    return res.json();
  } finally {
    endRequest();
  }
}

// --- Screen helpers ---
function show(id) {
  for (const el of document.querySelectorAll(".screen")) {
    el.style.display = "none";
  }
  const target = document.getElementById(id);
  if (target) target.style.display = "";
  const landingVisible = id === "auth-screen";
  document.body.classList.toggle("auth-visible", landingVisible);
  updateShell({
    screenId: id,
    user: getUser(),
    hasSession: Boolean(currentSessionId),
  });
  if (!landingVisible) {
    const dialog = document.getElementById("auth-dialog");
    if (dialog) closeDialog(dialog, { restoreFocus: false });
    document.body.classList.remove("auth-modal-open");
    requestAnimationFrame(() => {
      target?.setAttribute("tabindex", "-1");
      target?.focus({ preventScroll: true });
    });
  }
}

function setMsg(id, text, kind) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text || "";
  el.className = (el.dataset.messageClass || "msg") + (kind ? " " + kind : "");
  el.setAttribute("aria-live", kind === "error" ? "assertive" : "polite");
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  if (text && kind === "ok") showToast(text, "success");
  if (text && kind === "error") showToast(text, "error", 6500);
}

// --- Game log ---
const output = () => document.getElementById("output");

function appendLog(who, body, cls) {
  const out = output();
  if (!out) return;
  const entry = document.createElement("div");
  entry.className = "entry " + (cls || "");
  const w = document.createElement("span");
  w.className = "who";
  w.textContent = who === "DM" ? "Mestre" : who;
  const b = document.createElement("span");
  b.className = "body";
  b.textContent = body;
  entry.appendChild(w);
  entry.appendChild(b);
  out.appendChild(entry);
  out.scrollTop = out.scrollHeight;
}

function clearLog() {
  const out = output();
  if (out) out.innerHTML = "";
}

// --- Current session state ---
let currentSessionId = null;
let currentSlug = null;
let currentGameState = null;
// Phase 36: which character's sheet is currently visible in the
// tabbed ficha panel. Survives across renderCharacterTools() calls
// so the player's view doesn't snap back to the player after every
// input. Reset only when a new game session starts.
let activeSheetId = null;
// Read-only view (admin inspecting a save). When true, input is disabled
// and sendInput() bails out — the admin can look but not play.
let readOnlyMode = false;
let quotaReached = false;
let sessionExpired = false;

// --- Phase 28: input-blocking + busy feedback ---
let busy = false;

function lockUi() {
  if (busy) return;
  busy = true;
  syncGameSessionState();
}

function unlockUi() {
  busy = false;
  syncGameSessionState();
}

function currentGameUiState() {
  if (readOnlyMode) return { state: "readonly", label: "Somente leitura" };
  if (sessionExpired || !currentSessionId) {
    return { state: "expired", label: "Sessão expirada" };
  }
  if (!online()) return { state: "offline", label: "Aguardando conexão" };
  if (quotaReached) return { state: "quota", label: "Cota diária atingida" };
  if (busy) return { state: "thinking", label: "Mestre pensando…" };
  return { state: "ready", label: "Seu turno" };
}

function syncGameSessionState() {
  const screen = document.getElementById("game-screen");
  const status = document.getElementById("game-session-status");
  const label = document.getElementById("game-status-label");
  if (!screen || !status || !label) return;
  const ui = currentGameUiState();
  screen.dataset.sessionState = ui.state;
  status.dataset.state = ui.state;
  label.textContent = ui.label;

  const blocked = ui.state !== "ready";
  for (const id of ["cmd", "send-btn", "roll-btn", "roll-check", "roll-adv", "roll-dis"]) {
    const control = document.getElementById(id);
    if (control) control.disabled = blocked;
  }
  for (const control of screen.querySelectorAll("[data-command]")) {
    control.disabled = blocked;
  }
}

function updateGameContext() {
  const campaign = document.getElementById("game-campaign-name");
  const location = document.getElementById("game-location");
  if (campaign) {
    campaign.textContent = currentGameState?.campaign_name || currentSlug || "Mesa de jogo";
  }
  if (location) {
    const place = currentGameState?.current_location || "Local desconhecido";
    const combat = currentGameState?.in_combat
      ? ` · Combate, rodada ${currentGameState.round_number || 1}`
      : "";
    location.textContent = `${place}${combat}`;
  }
}

function showTyping() {
  const out = output();
  if (!out) return;
  const entry = document.createElement("div");
  entry.className = "entry typing-indicator";
  entry.id = "typing-indicator";
  const w = document.createElement("span");
  w.className = "who";
  w.textContent = "Mestre";
  const b = document.createElement("span");
  b.className = "body";
  for (let i = 0; i < 3; i++) {
    const dot = document.createElement("span");
    dot.className = "dot";
    b.appendChild(dot);
  }
  entry.appendChild(w);
  entry.appendChild(b);
  out.appendChild(entry);
  out.scrollTop = out.scrollHeight;
}

function hideTyping() {
  const el = document.getElementById("typing-indicator");
  if (el) el.remove();
}

// --- Auth handlers ---
let authMode = "login";

function setAuthMode(mode) {
  authMode = mode === "signup" ? "signup" : "login";
  const signup = authMode === "signup";
  const title = document.getElementById("auth-title");
  const subtitle = document.getElementById("auth-subtitle");
  const loginTab = document.getElementById("auth-mode-login");
  const signupTab = document.getElementById("auth-mode-signup");
  const loginBtn = document.getElementById("login-btn");
  const signupBtn = document.getElementById("signup-btn");
  const inviteWrap = document.getElementById("auth-invite-wrap");
  const password = document.getElementById("auth-password");

  title.textContent = signup ? "Comece sua jornada" : "Bem-vindo de volta";
  subtitle.textContent = signup
    ? "Crie sua conta e prepare seu primeiro personagem."
    : "Sua campanha está esperando por você.";
  loginTab.classList.toggle("active", !signup);
  signupTab.classList.toggle("active", signup);
  loginTab.setAttribute("aria-selected", String(!signup));
  signupTab.setAttribute("aria-selected", String(signup));
  loginBtn.hidden = signup;
  signupBtn.hidden = !signup;
  inviteWrap.hidden = !signup;
  password.autocomplete = signup ? "new-password" : "current-password";
  setMsg("auth-msg", "", "");
}

function openAuthDialog(mode) {
  const dialog = document.getElementById("auth-dialog");
  setAuthMode(mode);
  document.body.classList.add("auth-modal-open");
  openDialog(dialog, { initialFocus: "#auth-username" });
}

function closeAuthDialog(restoreFocus = true) {
  const dialog = document.getElementById("auth-dialog");
  if (!dialog || dialog.hidden) return;
  closeDialog(dialog, { restoreFocus });
  document.body.classList.remove("auth-modal-open");
}

function setAuthBusy(isBusy) {
  const form = document.getElementById("auth-form");
  if (form) form.setAttribute("aria-busy", String(isBusy));
  for (const id of ["login-btn", "signup-btn"]) {
    const button = document.getElementById(id);
    if (button) button.disabled = isBusy;
  }
}

function authErrorMessage(error) {
  if (error.status === 401) return "Usuário ou senha incorretos.";
  if (error.status === 403) return "Código de convite inválido ou ausente.";
  if (error.status === 409) return "Esse nome de aventureiro já está em uso.";
  if (error.status === 422) {
    return "Confira o nome e use uma senha com pelo menos 8 caracteres.";
  }
  return "Não foi possível atravessar os portões. Tente novamente.";
}

async function doSignup() {
  const username = document.getElementById("auth-username").value.trim();
  const password = document.getElementById("auth-password").value;
  const inviteCode = document.getElementById("auth-invite").value.trim();
  if (!username || !password) {
    setMsg("auth-msg", "Preencha usuário e senha.", "error");
    return;
  }
  setAuthBusy(true);
  try {
    const body = { username, password };
    if (inviteCode) body.invite_code = inviteCode;
    const res = await api("/api/auth/signup", {
      method: "POST",
      body,
    });
    setToken(res.token);
    setUser(res.user);
    afterLogin();
  } catch (e) {
    setMsg("auth-msg", authErrorMessage(e), "error");
  } finally {
    setAuthBusy(false);
  }
}

async function doLogin() {
  const username = document.getElementById("auth-username").value.trim();
  const password = document.getElementById("auth-password").value;
  if (!username || !password) {
    setMsg("auth-msg", "Preencha usuário e senha.", "error");
    return;
  }
  setAuthBusy(true);
  try {
    const res = await api("/api/auth/login", {
      method: "POST",
      body: { username, password },
    });
    setToken(res.token);
    setUser(res.user);
    afterLogin();
  } catch (e) {
    setMsg("auth-msg", authErrorMessage(e), "error");
  } finally {
    setAuthBusy(false);
  }
}

function doLogout() {
  setToken(null);
  setUser(null);
  currentSessionId = null;
  currentSlug = null;
  currentGameState = null;
  readOnlyMode = false;
  setShellUser(null);
  show("auth-screen");
}

function afterLogin() {
  closeAuthDialog(false);
  const u = getUser();
  setShellUser(u);
  loadLobby();
}

// --- Lobby: list saves ---
let lobbyTab = "active";
let lobbyRequestId = 0;

// Admins list saves across all users (with owner); regular users list
// only their own. The base path switches accordingly.
const savesEndpoint = (archived) =>
  isAdmin()
    ? `/api/admin/saves${archived ? "?archived=true" : ""}`
    : `/api/saves${archived ? "?archived=true" : ""}`;

async function loadLobby() {
  show("lobby-screen");
  // "Criar jogo vazio" (advanced option) is admin-only.
  const advanced = document.querySelector(".lobby-advanced");
  if (advanced) advanced.hidden = !isAdmin();
  lobbyTab = "active";
  await loadLobbyTab("active");
}

function isOwnSave(s) {
  if (!isAdmin()) return true;
  const currentUser = getUser();
  return !!(currentUser && Number(s.user_id) === Number(currentUser.id));
}

function updateLobbyTabs(tab, state = "ready") {
  const active = tab === "active";
  const activeTab = document.getElementById("lobby-active-tab");
  const archivedTab = document.getElementById("lobby-archived-tab");
  const activePanel = document.getElementById("lobby-active-panel");
  const archivedPanel = document.getElementById("lobby-archived-panel");
  const showPanel = state === "ready";

  activeTab.setAttribute("aria-selected", String(active));
  archivedTab.setAttribute("aria-selected", String(!active));
  activeTab.tabIndex = active ? 0 : -1;
  archivedTab.tabIndex = active ? -1 : 0;
  activePanel.hidden = !showPanel || !active;
  archivedPanel.hidden = !showPanel || active;
}

function setLobbyViewState(state) {
  document.getElementById("lobby-loading").hidden = state !== "loading";
  document.getElementById("lobby-error").hidden = state !== "error";
  updateLobbyTabs(lobbyTab, state === "ready" ? "ready" : state);
}

async function loadLobbyTab(tab) {
  lobbyTab = tab === "archived" ? "archived" : "active";
  const archived = lobbyTab === "archived";
  const requestId = ++lobbyRequestId;
  setLobbyViewState("loading");
  setMsg("lobby-msg", "", "");
  try {
    const saves = await api(savesEndpoint(archived));
    if (requestId !== lobbyRequestId) return;
    renderCampaignList(saves, { archived });
    setLobbyViewState("ready");
  } catch (e) {
    if (requestId !== lobbyRequestId) return;
    document.getElementById("lobby-error-detail").textContent = navigator.onLine
      ? "Tente novamente. Se o problema continuar, volte em alguns instantes."
      : "Sua conexão parece estar offline. Reconecte-se e tente novamente.";
    setLobbyViewState("error");
    setMsg("lobby-msg", "Não foi possível carregar as campanhas: " + e.message, "error");
  }
}

function setLobbyCount(archived, count) {
  const counter = document.getElementById(archived ? "archived-count" : "active-count");
  counter.textContent = String(count);
  counter.hidden = false;
}

function createLobbyIcon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon icon-compact");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `/assets/icons/lucide.svg#${name}`);
  svg.appendChild(use);
  return svg;
}

function campaignAction(label, icon, className, onclick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `button ${className}`;
  button.append(createLobbyIcon(icon), document.createTextNode(label));
  button.onclick = onclick;
  return button;
}

function formatSaveDate(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "Data não disponível";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function appendCampaignMeta(list, icon, text, dateTime = "") {
  const item = document.createElement("li");
  item.appendChild(createLobbyIcon(icon));
  if (dateTime) {
    const time = document.createElement("time");
    time.dateTime = dateTime;
    time.textContent = text;
    time.title = dateTime;
    item.appendChild(time);
  } else {
    const value = document.createElement("span");
    value.textContent = text;
    item.appendChild(value);
  }
  list.appendChild(item);
}

function renderCampaignList(saves, { archived }) {
  const list = document.getElementById(archived ? "archived-list" : "saves-list");
  const empty = document.getElementById(archived ? "archived-empty" : "lobby-empty");
  list.innerHTML = "";
  list.hidden = saves.length === 0;
  empty.hidden = saves.length !== 0;
  setLobbyCount(archived, saves.length);
  const featuredIndex = archived ? -1 : saves.findIndex((save) => isOwnSave(save));
  saves.forEach((save, index) => {
    list.appendChild(renderSaveRow(save, { archived, featured: index === featuredIndex }));
  });
}

// Build one dense campaign row. The newest campaign owned by the logged-in
// user receives the primary "Continuar aventura" action.
function renderSaveRow(s, { archived, featured = false }) {
  const ownSave = isOwnSave(s);
  const li = document.createElement("li");
  li.className = "campaign-row" + (featured && ownSave ? " campaign-row-featured" : "");

  const summary = document.createElement("div");
  summary.className = "campaign-summary";
  if (featured && ownSave) {
    const eyebrow = document.createElement("p");
    eyebrow.className = "campaign-eyebrow";
    eyebrow.textContent = "Aventura mais recente";
    summary.appendChild(eyebrow);
  }

  const titleLine = document.createElement("div");
  titleLine.className = "campaign-title-line";
  const title = document.createElement("h3");
  title.className = "campaign-title";
  title.textContent = s.campaign_name || s.slug;
  titleLine.appendChild(title);
  if (isAdmin() && s.username) {
    const owner = document.createElement("span");
    owner.className = "campaign-owner";
    owner.textContent = `@${s.username}`;
    titleLine.appendChild(owner);
  }
  if (archived) {
    const tag = document.createElement("span");
    tag.className = "campaign-archived-tag";
    tag.textContent = "Arquivada";
    titleLine.appendChild(tag);
  }
  summary.appendChild(titleLine);

  const slug = document.createElement("p");
  slug.className = "campaign-slug";
  slug.textContent = `Save: ${s.slug}`;
  summary.appendChild(slug);

  const metadata = document.createElement("ul");
  metadata.className = "campaign-meta";
  const character = s.character_name
    ? `${s.character_name}${s.character_level ? ` · Nível ${s.character_level}` : ""}`
    : "Sem personagem";
  appendCampaignMeta(metadata, "user", character);
  appendCampaignMeta(metadata, "map-pin", s.current_location || "Local ainda não revelado");
  appendCampaignMeta(metadata, "clock", `Atualizada em ${formatSaveDate(s.updated_at)}`, s.updated_at);
  summary.appendChild(metadata);

  const actions = document.createElement("div");
  actions.className = "campaign-actions";
  let openButton;
  if (!ownSave) {
    openButton = campaignAction(
      "Visualizar",
      "arrow-right",
      "button-secondary campaign-open",
      () => viewSaveReadOnly(s.user_id, s.slug),
    );
  } else {
    openButton = campaignAction(
      featured ? "Continuar aventura" : archived ? "Abrir arquivada" : "Abrir campanha",
      "arrow-right",
      `${featured ? "button-primary campaign-open-featured" : "button-secondary"} campaign-open`,
      () => loadSaveAsSession(s.slug),
    );
  }
  actions.appendChild(openButton);

  if (ownSave) {
    if (archived) {
      actions.appendChild(campaignAction(
        "Restaurar", "rotate-ccw", "button-ghost", () => unarchiveSave(s.slug),
      ));
    } else {
      actions.appendChild(campaignAction(
        "Arquivar", "archive", "button-ghost", () => archiveSave(s.slug),
      ));
    }
  }

  // Admin-only delete (works on archived and non-archived alike).
  if (isAdmin()) {
    actions.appendChild(campaignAction(
      "Excluir",
      "trash",
      "button-ghost campaign-action-danger",
      () => deleteSaveAdmin(s.user_id, s.slug),
    ));
  }
  li.append(summary, actions);
  return li;
}

// Admin read-only inspection of any user's save. Fetches a static
// snapshot (no session, no LLM) and renders the narrative log with
// input disabled.
async function viewSaveReadOnly(userId, slug) {
  try {
    const res = await api(
      `/api/admin/saves/${encodeURIComponent(userId)}/${encodeURIComponent(slug)}`,
    );
    currentGameState = res.state || null;
    enterGame({
      readOnly: true,
      narrativeLog: res.narrative_log || [],
      ownerLabel: res.username ? `${res.username}/${slug}` : slug,
    });
  } catch (e) {
    setMsg("lobby-msg", "Erro ao visualizar: " + e.message, "error");
  }
}

async function deleteSaveAdmin(userId, slug) {
  if (!await confirmAction(`Excluir o save “${slug}” definitivamente?`, {
    confirmLabel: "Excluir save",
  })) return;
  try {
    await api(
      `/api/admin/saves/${encodeURIComponent(userId)}/${encodeURIComponent(slug)}`,
      { method: "DELETE" },
    );
    setMsg("lobby-msg", `“${slug}” excluído.`, "ok");
    await loadLobbyTab(lobbyTab);
  } catch (e) {
    setMsg("lobby-msg", "Erro ao excluir: " + e.message, "error");
  }
}

async function archiveSave(slug) {
  try {
    await api(`/api/saves/${encodeURIComponent(slug)}/archive`, { method: "POST" });
    setMsg("lobby-msg", `“${slug}” arquivado.`, "ok");
    await loadLobbyTab("active");
  } catch (e) {
    setMsg("lobby-msg", "Erro ao arquivar: " + e.message, "error");
  }
}

async function unarchiveSave(slug) {
  try {
    await api(`/api/saves/${encodeURIComponent(slug)}/unarchive`, { method: "POST" });
    setMsg("lobby-msg", `“${slug}” restaurado.`, "ok");
    await loadLobbyTab("archived");
  } catch (e) {
    setMsg("lobby-msg", "Erro ao restaurar: " + e.message, "error");
  }
}

async function loadSaveAsSession(slug) {
  try {
    const res = await api(`/api/saves/${encodeURIComponent(slug)}/load`, {
      method: "POST",
    });
    currentSessionId = res.session_id;
    currentSlug = slug;
    currentGameState = res.state || null;
    // Repassa o narrative_log persistido para que enterGame() o renderize.
    // Sem isso, o jogador só vê "Sessão iniciada" e perde todo o histórico.
    enterGame({
      narrativeLog: (res.state && res.state.narrative_log) || [],
    });
  } catch (e) {
    setMsg("lobby-msg", "Erro: " + e.message, "error");
  }
}

async function createEmptySession() {
  const slug = document.getElementById("new-slug").value.trim() || "untitled";
  const scenarioEl = document.getElementById("new-scenario");
  const initialScenario = scenarioEl ? scenarioEl.value.trim() : "";
  // Phase 26a: create a minimal GameState with a placeholder character.
  // Full character creation is in 26c (wizard).
  const state = {
    campaign_name: slug,
    current_location: "",
    // Cenário inicial opcional definido pelo admin. Vazio = mestre decide.
    initial_scenario: initialScenario,
    party: [],
    npcs: [],
    initiative_order: [],
    in_combat: false,
    current_turn: 0,
    turn_counter: 0,
    active_conditions: [],
    session_notes: "",
    timestamp: new Date().toISOString(),
  };
  try {
    const res = await api("/api/sessions", {
      method: "POST",
      body: { state },
    });
    currentSessionId = res.session_id;
    currentSlug = slug;
    currentGameState = res.state || null;
    // Persist the empty state as a save.
    await api("/api/saves", {
      method: "POST",
      body: { slug, state: res.state },
    });
    enterGame({ empty: true });
  } catch (e) {
    setMsg("lobby-msg", "Erro: " + e.message, "error");
  }
}

// ============================================================================
// Phase 38 — XP, party level, ASI modal
// ============================================================================
//
// ``award_party_xp`` (engine/progression.py) drives the auto-level loop
// for both combat kills (in CombatEngine.end_combat) and the
// player-facing `/award-xp <n>` meta-command. The frontend watches
// `state.party_xp` and `state.player.pending_asi` and reacts here.

const ASI_ABILITIES = [
  { key: "strength",     label: "Força (STR)" },
  { key: "dexterity",    label: "Destreza (DEX)" },
  { key: "constitution", label: "Constituição (CON)" },
  { key: "intelligence", label: "Inteligência (INT)" },
  { key: "wisdom",       label: "Sabedoria (WIS)" },
  { key: "charisma",     label: "Carisma (CHA)" },
];

// Compute the next PHB threshold and how much XP is needed to reach it.
// Mirrors XP_THRESHOLDS in engine/progression.py.
const PARTY_XP_THRESHOLDS = [
  0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000,
  85000, 100000, 120000, 140000, 165000, 195000, 225000, 265000, 305000, 355000,
];

function partyLevelFromXp(xp) {
  let lvl = 1;
  for (let i = 1; i < PARTY_XP_THRESHOLDS.length; i++) {
    if (xp >= PARTY_XP_THRESHOLDS[i]) lvl = i + 1;
    else break;
  }
  return Math.min(lvl, 20);
}

function xpToNextPartyLevel(xp) {
  const lvl = partyLevelFromXp(xp);
  if (lvl >= 20) return null;
  return PARTY_XP_THRESHOLDS[lvl] - xp;
}

// Refresh the small XP/level banner above the chat log.
function updateLevelupBanner() {
  const banner = document.getElementById("levelup-banner");
  if (!banner) return;
  const state = currentGameState;
  if (!state) {
    banner.style.display = "none";
    return;
  }
  const xp = Number(state.party_xp || 0);
  const lvl = partyLevelFromXp(xp);
  const remaining = xpToNextPartyLevel(xp);
  if (remaining === null) {
    banner.textContent = `Party: L${lvl} · ${xp.toLocaleString("pt-BR")} XP (cap L20).`;
  } else {
    banner.textContent =
      `Party: L${lvl} · ${xp.toLocaleString("pt-BR")} XP · ` +
      `próximo nível em ${remaining.toLocaleString("pt-BR")} XP.`;
  }
  banner.style.display = "block";
}

// Hide the banner (used during the read-only admin view).
function hideLevelupBanner() {
  const banner = document.getElementById("levelup-banner");
  if (banner) banner.style.display = "none";
}

// Run after every state mutation: if the player has a queued ASI, open
// the modal. Idempotent (the modal stays open across re-renders until
// the choice is confirmed or the level falls through).
function checkPendingASI() {
  if (readOnlyMode) return;
  const player = getPlayerCharacter();
  if (!player || !player.pending_asi || player.pending_asi.resolved) return;
  openASIModal(player);
}

function openASIModal(player) {
  const modal = document.getElementById("asi-modal");
  const pickers = document.getElementById("asi-pickers");
  const status = document.getElementById("asi-status");
  const msg = document.getElementById("asi-msg");
  if (!modal || !pickers || !status || !msg) return;

  status.textContent = `Personagem: ${player.name} · Nível ${player.level}.`;
  msg.textContent = "";
  // Pre-fill the pickers every time the modal opens.
  renderASIPickers(player);
  modal.dataset.characterId = player.id;
  openDialog(modal, { initialFocus: "#asi-confirm" });
}

function closeASIModal() {
  const modal = document.getElementById("asi-modal");
  if (modal) closeDialog(modal);
}

// ============================================================================
// Phase 41c — Reaction modal. When an enemy attack hits the player or an
// enemy casts a spell, the engine publishes ``pending_reaction`` on the
// character. We surface a modal listing eligible reactions with a 30s
// countdown; the player picks one (or passes) and we POST /reaction.
// ============================================================================

const REACTION_LABELS = {
  shield: "Shield (+5 CA, imune a Magic Missile)",
  counterspell: "Counterspell (anular magia)",
  hellish_rebuke: "Hellish Rebuke (2d10 fogo)",
  healing_word: "Healing Word (1d4 cura)",
  uncanny_dodge: "Uncanny Dodge (dano à metade)",
  parry: "Parry (reduzir dano)",
  opportunity_attack: "Ataque de oportunidade",
};

let reactionTimerId = null;

function checkPendingReaction() {
  if (readOnlyMode) return;
  // Only prompt the human player (companions handled by future heuristic).
  const player = getPlayerCharacter();
  if (!player || !player.pending_reaction) return;
  const pr = player.pending_reaction;
  if (pr.resolved) return;
  const eligible = Array.isArray(pr.reactions_eligible) ? pr.reactions_eligible : [];
  if (!eligible.length) return;
  openReactionModal(player, pr);
}

function openReactionModal(player, pending) {
  const modal = document.getElementById("reaction-modal");
  const options = document.getElementById("reaction-options");
  const promptEl = document.getElementById("reaction-prompt");
  const timerEl = document.getElementById("reaction-timer");
  const msg = document.getElementById("reaction-msg");
  if (!modal || !options || !promptEl || !timerEl || !msg) return;

  msg.textContent = "";
  promptEl.textContent = describeReactionTrigger(pending.trigger);
  options.innerHTML = "";
  (pending.reactions_eligible || []).forEach((kind) => {
    const btn = document.createElement("button");
    btn.className = "primary reaction-option";
    btn.textContent = REACTION_LABELS[kind] || kind;
    btn.onclick = () => resolveReaction(kind);
    options.appendChild(btn);
  });
  openDialog(modal, { initialFocus: ".reaction-option" });

  // 30s countdown → auto-pass. We track expiry from the server's
  // ``expires_at`` so client/server stay aligned even if the tab was
  // backgrounded, falling back to a local 30s timer.
  const ttlMs = Math.max(
    1000,
    Math.min(30000, ((pending.expires_at || 0) - Math.floor(Date.now() / 1000)) * 1000 || 30000),
  );
  let remaining = Math.ceil(ttlMs / 1000);
  timerEl.textContent = `${remaining}s`;
  if (reactionTimerId) clearInterval(reactionTimerId);
  reactionTimerId = setInterval(() => {
    remaining -= 1;
    timerEl.textContent = `${remaining}s`;
    if (remaining <= 0) {
      closeReactionModal(true);
    }
  }, 1000);
}

function describeReactionTrigger(trigger) {
  if (!trigger) return "Um gatilho de reação ocorreu.";
  if (trigger.kind === "on_hit_by_attack") {
    return `Você foi atingido por um ataque (${trigger.attack_damage} de dano${trigger.is_crit ? " crítico" : ""}). Reagir?`;
  }
  if (trigger.kind === "on_seeing_spell_cast") {
    return `${trigger.caster_id} está conjurando ${trigger.spell_name || "uma magia"} (${trigger.level}º). Reagir?`;
  }
  if (trigger.kind === "on_ally_down") {
    return `${trigger.ally_id} caiu. Reagir com cura?`;
  }
  if (trigger.kind === "on_damage_taken") {
    return `Você sofreu ${trigger.amount} de dano. Reagir?`;
  }
  return "Um gatilho de reação ocorreu.";
}

function closeReactionModal(decline) {
  const modal = document.getElementById("reaction-modal");
  if (modal) closeDialog(modal);
  if (reactionTimerId) {
    clearInterval(reactionTimerId);
    reactionTimerId = null;
  }
  if (decline) resolveReaction(null);
}

async function resolveReaction(kind) {
  if (reactionTimerId) {
    clearInterval(reactionTimerId);
    reactionTimerId = null;
  }
  const modal = document.getElementById("reaction-modal");
  const msg = document.getElementById("reaction-msg");
  if (modal) closeDialog(modal);
  // Passing (no kind): tell the server to clear the trigger without
  // resolving — but only if one is actually open.
  const payload = kind ? { kind } : { decline: true };
  try {
    const res = await api(`/api/sessions/${currentSessionId}/reaction`, {
      method: "POST",
      body: payload,
    });
    if (res.state) {
      currentGameState = res.state;
      renderCharacterTools();
      await persistSave(res.state);
    }
    if (res.declined) return;
    const r = res.resolution || {};
    if (r.message) appendLog(r.success === false ? "Sistema" : (res.character_name || "Reação"),
      r.message, r.success === false ? "system" : "companion");
  } catch (e) {
    if (e && e.status === 408) {
      // Window expired server-side — nothing to do.
      return;
    }
    if (e && e.status === 404) return;  // nothing pending
    if (msg) msg.textContent = e.message || "Falha ao resolver reação.";
    if (modal) openDialog(modal, { initialFocus: ".reaction-option" });
  }
}

function renderASIPickers(player) {
  const pickers = document.getElementById("asi-pickers");
  if (!pickers) return;
  pickers.innerHTML = "";
  const mode = (document.querySelector('input[name="asi-mode"]:checked') || {}).value || "plus2";

  ASI_ABILITIES.forEach((ab) => {
    const wrap = document.createElement("label");
    wrap.className = "asi-pick";
    const input = document.createElement("input");
    if (mode === "plus2") {
      input.type = "radio";
      input.name = "asi-primary";
      input.value = ab.key;
    } else {
      input.type = "checkbox";
      input.name = "asi-secondary";
      input.value = ab.key;
    }
    const cur = (player.abilities && player.abilities[ab.key]) ?? 10;
    const cap = cur >= 20 && mode === "plus2" ? " (cap)" : "";
    const text = document.createTextNode(` ${ab.label} (atual: ${cur}${cap})`);
    wrap.appendChild(input);
    wrap.appendChild(text);
    pickers.appendChild(wrap);
  });

  // Wiring: switching modes re-renders the picker list. The mode radios
  // live in .asi-mode-row (outside #asi-pickers), so query the document.
  document.querySelectorAll('input[name="asi-mode"]').forEach((r) => {
    r.onchange = () => renderASIPickers(player);
  });
}

async function confirmASI() {
  const modal = document.getElementById("asi-modal");
  const msg = document.getElementById("asi-msg");
  if (!modal || !msg) return;
  const characterId = modal.dataset.characterId;
  if (!characterId) return;

  const mode = (document.querySelector('input[name="asi-mode"]:checked') || {}).value || "plus2";
  let primary = null;
  let secondary = null;
  if (mode === "plus2") {
    const sel = document.querySelector('input[name="asi-primary"]:checked');
    if (!sel) {
      msg.textContent = "Selecione o atributo que vai receber +2.";
      return;
    }
    primary = sel.value;
  } else {
    const checked = Array.from(document.querySelectorAll('input[name="asi-secondary"]:checked'));
    if (checked.length !== 2) {
      msg.textContent = `Selecione exatamente dois atributos (você escolheu ${checked.length}).`;
      return;
    }
    primary = checked[0].value;
    secondary = checked[1].value;
  }

  msg.textContent = "Aplicando…";
  const btn = document.getElementById("asi-confirm");
  if (btn) btn.disabled = true;
  try {
    await resolveASI(characterId, primary, secondary);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function resolveASI(characterId, primary, secondary) {
  try {
    const body = { character_id: characterId, primary };
    if (secondary) body.secondary = secondary;
    const res = await api(
      `/api/sessions/${currentSessionId}/resolve-asi`,
      { method: "POST", body },
    );
    if (res && res.state) {
      currentGameState = res.state;
    }
    renderCharacterTools();
    updateLevelupBanner();
    closeASIModal();
    appendLog(
      "Sistema",
      secondary
        ? `ASI aplicada: +1 ${primary}, +1 ${secondary} em ${res.character_name}.`
        : `ASI aplicada: +2 ${primary} em ${res.character_name}.`,
      "system",
    );
  } catch (e) {
    setMsg("asi-msg", e.message, "error");
  }
}

// Called from /award-xp (and indirectly from `/input` after combat ends).
async function awardXP(amount) {
  try {
    const res = await api(
      `/api/sessions/${currentSessionId}/award-xp`,
      { method: "POST", body: { amount } },
    );
    if (res && res.state) {
      currentGameState = res.state;
    }
    renderCharacterTools();
    updateLevelupBanner();
    appendLog(
      "Sistema",
      `+${amount} XP concedidos à party (total: ${res.new_party_xp.toLocaleString("pt-BR")}; ` +
        `nível ${res.old_party_level} → ${res.new_party_level}).`,
      "system",
    );
    if (res.any_leveled && Array.isArray(res.reports)) {
      for (const r of res.reports) {
        appendLog(
          "Sistema",
          `${r.character_name} subiu para o nível ${r.new_level} (HP +${r.hp_gained}).`,
          "system",
        );
      }
    }
    // If the player got a queued ASI, pop the modal.
    if (res.any_asi_pending) {
      checkPendingASI();
    }
  } catch (e) {
    appendLog("Erro", e.message, "system");
  }
}
// opts:
//   readOnly     — admin inspecting a save; input disabled, narrative
//                  log replayed from the snapshot, no session/LLM.
//   narrativeLog — array of NarrativeEntry {role, speaker, content}.
//   ownerLabel   — "<username>/<slug>" shown in the read-only banner.
//   empty        — true for the admin "empty game" path (no character);
//                  shows the manual-start hint instead of auto-opening.
function enterGame(opts = {}) {
  const { readOnly = false, narrativeLog = [], ownerLabel = "", empty = false } = opts;
  readOnlyMode = readOnly;
  quotaReached = false;
  sessionExpired = false;
  // Reset which character sheet is visible — only persists within the
  // same game session (between /input calls). A new session always
  // opens on the player tab.
  activeSheetId = null;
  show("game-screen");
  clearLog();
  renderCharacterTools();
  updateGameContext();
  selectGamePanel("narrative", { focus: false });
  syncGameSessionState();

  if (readOnly) {
    // Disable all input controls; lobby button stays usable.
    syncGameSessionState();
    appendLog(
      "Sistema",
      `Modo visualização (somente leitura — admin)${ownerLabel ? ` · ${ownerLabel}` : ""}.`,
      "system",
    );
    renderNarrativeLog(narrativeLog);
    if (narrativeLog.length === 0) {
      appendLog("Sistema", "(sem histórico narrado neste save)", "system");
    }
    hideLevelupBanner();  // Phase 38 — admin viewer doesn't show XP banner
    return;
  }

  appendLog("Sistema",
    `Sessão iniciada${currentSlug ? ` (save: ${currentSlug})` : ""}.`,
    "system");
  if (empty) {
    appendLog("Sistema",
      "Jogo vazio iniciado — sem personagem definido. " +
      "Por enquanto, digite algo para começar (a IA narrará mesmo sem personagem).",
      "system");
  }
  // Renderiza o histórico narrativo persistido (carregado de save).
  // Para jogos novos (wizard) e sessão vazia, narrativeLog está vazio e
  // o loop é no-op — sem render duplicado da abertura.
  if (narrativeLog && narrativeLog.length) {
    renderNarrativeLog(narrativeLog);
  }
  // Phase 38 — surface any pending ASI choice made during the last
  // turn, and refresh the XP/level banner.
  checkPendingASI();
  updateLevelupBanner();
  initMusicPlayer();  // Phase 42 — ambient music (if enabled in prefs)
}

// Persist the current GameState to the user's save (Postgres). Best-effort.
async function persistSave(state) {
  if (!state) return;
  currentGameState = state;
  updateGameContext();
  renderCharacterTools();
  if (!currentSlug) return;
  try {
    await api("/api/saves", {
      method: "POST",
      body: { slug: currentSlug, state },
    });
  } catch (_) {
    /* best-effort; the session state in Redis is still authoritative */
  }
}

// Auto-generate the campaign opening narration right after a fresh game
// is created, so the player sees the first scene without typing anything.
// Idempotent on the backend (a re-call on an already-opened game just
// returns the existing narration).
async function playOpening() {
  if (readOnlyMode || !currentSessionId) return;
  lockUi();
  showTyping();
  try {
    await playOpeningClassic();
  } finally {
    unlockUi();
    hideTyping();
  }
}

async function playOpeningClassic() {
  try {
    const res = await api(`/api/sessions/${currentSessionId}/opening`, {
      method: "POST",
    });
    if (res.narration) {
      appendLog("DM", res.narration, "narration");
      lastDmNarration = res.narration;  // Phase 42 — 🔊 replay + auto-TTS
      refreshAudioButtons();
      maybeAutoPlayTTS(res.narration);
    }
    if (res.state) {
      currentGameState = res.state;
      updateLevelupBanner();
      checkPendingASI();
      await persistSave(res.state);
    }
  } catch (e) {
    if (e && e.status === 429) {
      quotaReached = true;
      syncGameSessionState();
      appendLog("Sistema", LIMIT_REACHED_MSG, "system");
      return;
    }
    if (e && (e.status === 401 || e.status === 404)) {
      sessionExpired = true;
      syncGameSessionState();
      appendLog("Sistema", "Esta sessão expirou. Volte às campanhas para continuar.", "system");
      return;
    }
    appendLog("Erro", "Não foi possível gerar a abertura: " + e.message, "system");
  }
}

// Render a GameState.narrative_log into the game output. Maps each
// NarrativeEntry {role, speaker, content} onto the appendLog styling.
function renderNarrativeLog(entries) {
  for (const e of entries || []) {
    const cls =
      e.role === "player" ? "player"
      : e.role === "companion" ? "companion"
      : e.role === "dm" ? "narration"
      : "system";
    const who = e.speaker || (e.role === "dm" ? "DM" : "Sistema");
    if (e.content) appendLog(who, e.content, cls);
  }
}

// ============================================================================
// Character sheet + virtual check roller
// ============================================================================

const ABILITY_OPTIONS = [
  { key: "strength", short: "FOR", label: "Forca" },
  { key: "dexterity", short: "DES", label: "Destreza" },
  { key: "constitution", short: "CON", label: "Constituicao" },
  { key: "intelligence", short: "INT", label: "Inteligencia" },
  { key: "wisdom", short: "SAB", label: "Sabedoria" },
  { key: "charisma", short: "CAR", label: "Carisma" },
];

const SKILL_OPTIONS = [
  { key: "acrobatics", label: "Acrobacia", ability: "dexterity" },
  { key: "animal_handling", label: "Adestrar Animais", ability: "wisdom" },
  { key: "arcana", label: "Arcanismo", ability: "intelligence" },
  { key: "athletics", label: "Atletismo", ability: "strength" },
  { key: "deception", label: "Enganacao", ability: "charisma" },
  { key: "history", label: "Historia", ability: "intelligence" },
  { key: "insight", label: "Intuicao", ability: "wisdom" },
  { key: "intimidation", label: "Intimidacao", ability: "charisma" },
  { key: "investigation", label: "Investigacao", ability: "intelligence" },
  { key: "medicine", label: "Medicina", ability: "wisdom" },
  { key: "nature", label: "Natureza", ability: "intelligence" },
  { key: "perception", label: "Percepcao", ability: "wisdom" },
  { key: "performance", label: "Atuacao", ability: "charisma" },
  { key: "persuasion", label: "Persuasao", ability: "charisma" },
  { key: "religion", label: "Religiao", ability: "intelligence" },
  { key: "sleight_of_hand", label: "Prestidigitacao", ability: "dexterity" },
  { key: "stealth", label: "Furtividade", ability: "dexterity" },
  { key: "survival", label: "Sobrevivencia", ability: "wisdom" },
];

function fmtMod(n) {
  return `${n >= 0 ? "+" : ""}${n}`;
}

// Minimal HTML-escape for text injected via innerHTML in the character
// sheet (tab labels + free-text fields like name/race/class). We use
// textContent everywhere else — this exists specifically because the
// sheet panel builds HTML strings for performance (innerHTML batch).
function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function abilityShort(key) {
  return ABILITY_OPTIONS.find((a) => a.key === key)?.short || key;
}

function abilityLabel(key) {
  return ABILITY_OPTIONS.find((a) => a.key === key)?.label || key;
}

function skillLabel(key) {
  return SKILL_OPTIONS.find((s) => s.key === key)?.label || key;
}

function scoreModifier(score) {
  return Math.floor((Number(score || 10) - 10) / 2);
}

function getPlayerCharacter() {
  const st = currentGameState;
  if (!st || !Array.isArray(st.party)) return null;
  return st.party.find((c) => c.id === st.player_character_id)
    || st.party.find((c) => c.is_player)
    || st.party[0]
    || null;
}

function hasProficiency(character, kind, key) {
  const profs = character?.proficiencies || {};
  if (kind === "skill") return (profs.skills || []).includes(key);
  if (kind === "save") return (profs.saves || []).includes(key);
  return false;
}

function modifierForSelection(character, kind, key) {
  if (!character) return null;
  const ability = kind === "skill"
    ? SKILL_OPTIONS.find((s) => s.key === key)?.ability
    : key;
  const abilityMod = scoreModifier(character.abilities?.[ability]);
  const proficient = hasProficiency(character, kind, key);
  const profBonus = proficient ? Number(character.proficiency_bonus || 0) : 0;
  return {
    ability,
    abilityMod,
    proficient,
    profBonus,
    total: abilityMod + profBonus,
  };
}

function populateRollOptions() {
  const select = document.getElementById("roll-check");
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";

  const skills = document.createElement("optgroup");
  skills.label = "Pericias";
  for (const skill of SKILL_OPTIONS) {
    const opt = document.createElement("option");
    opt.value = `skill:${skill.key}`;
    opt.textContent = `${skill.label} (${abilityShort(skill.ability)})`;
    skills.appendChild(opt);
  }
  select.appendChild(skills);

  const abilities = document.createElement("optgroup");
  abilities.label = "Testes de atributo";
  for (const ability of ABILITY_OPTIONS) {
    const opt = document.createElement("option");
    opt.value = `ability:${ability.key}`;
    opt.textContent = `${ability.label} (${ability.short})`;
    abilities.appendChild(opt);
  }
  select.appendChild(abilities);

  const saves = document.createElement("optgroup");
  saves.label = "Salvaguardas";
  for (const ability of ABILITY_OPTIONS) {
    const opt = document.createElement("option");
    opt.value = `save:${ability.key}`;
    opt.textContent = `Salvaguarda de ${ability.label} (${ability.short})`;
    saves.appendChild(opt);
  }
  select.appendChild(saves);

  if (previous) select.value = previous;
}

function compactCharacterResources(character) {
  const resources = [];
  const remainingRages = Math.max(0, Number(character.rages_max || 0) - Number(character.rages_used || 0));
  if (character.rages_max) resources.push(`Fúria ${remainingRages}/${character.rages_max}`);
  if (character.ki_max) resources.push(`Ki ${character.ki_points || 0}/${character.ki_max}`);
  if (character.sorcery_points_max) {
    resources.push(`Feitiçaria ${character.sorcery_points || 0}/${character.sorcery_points_max}`);
  }
  if (Number(character.lay_on_hands_pool) > 0) {
    resources.push(`Mãos Curadoras ${character.lay_on_hands_pool}`);
  }
  if (character.bardic_inspiration_max) {
    resources.push(`Inspiração ${character.bardic_inspiration_uses || 0}/${character.bardic_inspiration_max}`);
  }
  if (Number(character.channel_divinity_remaining) > 0) {
    resources.push(`Canalizar ${character.channel_divinity_remaining}`);
  }
  if (Number(character.action_surges_remaining) > 0) {
    resources.push(`Surto ${character.action_surges_remaining}`);
  }
  const slots = character.spellcasting?.spell_slots || {};
  const availableSlots = Object.values(slots).reduce((total, value) => total + Number(value || 0), 0);
  if (availableSlots > 0) resources.push(`Slots ${availableSlots}`);
  return resources.slice(0, 2);
}

function renderPartyOverview(chars) {
  const host = document.getElementById("party-overview");
  if (!host) return;
  host.innerHTML = chars.map((character) => {
    const hp = Math.max(0, Number(character.hp_current || 0));
    const hpMax = Math.max(1, Number(character.hp_max || 1));
    const hpRatio = hp / hpMax;
    const healthClass = hpRatio <= 0.25 ? " is-critical" : hpRatio <= 0.5 ? " is-hurt" : "";
    const conditions = (character.conditions || []).slice(0, 2);
    const conditionHtml = conditions.length
      ? conditions.map((condition) =>
          `<span class="party-card-condition">${escapeHtml(condition)}</span>`).join("")
      : '<span class="party-card-role">Sem condições</span>';
    const resources = compactCharacterResources(character);
    const resourceHtml = resources.length
      ? resources.map((resource) => `<span class="party-resource">${escapeHtml(resource)}</span>`).join("")
      : '<span class="party-resource">Recursos estáveis</span>';
    const talkAction = character.is_player
      ? '<button type="button" class="party-quick-action" data-command="/status">Ver situação</button>'
      : `<button type="button" class="party-quick-action" data-command="falo com ${escapeHtml(character.name)}">Conversar</button>`;
    return (
      `<article class="party-card${healthClass}" data-party-card="${escapeHtml(character.id)}">` +
        `<div class="party-card-head"><span class="party-card-name">${escapeHtml(character.name)}</span>` +
        `<span class="party-card-role">${character.is_player ? "Você" : "Companheiro"}</span></div>` +
        `<div class="party-card-vitals"><div class="party-hp">` +
        `<span class="party-hp-label"><span>PV</span><strong>${hp}/${hpMax}</strong></span>` +
        `<progress class="party-hp-track" value="${hp}" max="${hpMax}">${hp} de ${hpMax}</progress>` +
        `</div><span class="party-ac">CA ${character.armor_class ?? "—"}</span></div>` +
        `<div class="party-card-vitals">${conditionHtml}${resourceHtml}</div>` +
        `<div class="party-card-actions">` +
        `<button type="button" class="party-quick-action" data-open-sheet="${escapeHtml(character.id)}">Ver ficha</button>` +
        `${talkAction}</div>` +
      `</article>`
    );
  }).join("");

  for (const button of host.querySelectorAll("[data-open-sheet]")) {
    button.addEventListener("click", () => setActiveSheetTab(button.dataset.openSheet));
  }
  for (const button of host.querySelectorAll("[data-command]")) {
    button.addEventListener("click", () => {
      fillCommand(button.dataset.command || "");
      selectGamePanel("narrative");
    });
  }
}

function renderCharacterTools() {
  const tools = document.getElementById("table-tools");
  if (!tools) return;
  const player = getPlayerCharacter();
  const party = (currentGameState && Array.isArray(currentGameState.party)) ? currentGameState.party : [];
  const companions = party.filter((c) => !c.is_player);
  const chars = player ? [player, ...companions] : companions;
  if (chars.length === 0) {
    tools.style.display = "none";
    tools.classList.remove("is-mobile-open");
    for (const panel of ["party", "roll"]) {
      const tab = document.querySelector(`[data-game-panel="${panel}"]`);
      if (tab) tab.disabled = true;
    }
    return;
  }
  tools.style.display = "";
  for (const panel of ["party", "roll"]) {
    const tab = document.querySelector(`[data-game-panel="${panel}"]`);
    if (tab) tab.disabled = false;
  }
  populateRollOptions();

  const tabsHost = document.getElementById("char-tabs");
  const sheetHost = document.getElementById("sheet-host");
  if (!tabsHost || !sheetHost) return;

  tabsHost.innerHTML = chars
    .map((c) => {
      const cls = c.class || "";
      const sub = c.subclass ? ` · ${c.subclass}` : "";
      const isPlayerTag = c.is_player ? ' <span class="tab-tag">você</span>' : "";
      return (
        `<button type="button" class="char-tab" role="tab" data-char-id="${escapeHtml(c.id)}" title="${escapeHtml(c.name)}" aria-controls="sheet-${escapeHtml(c.id)}">` +
        `<span class="tab-name">${escapeHtml(c.name)}</span>` +
        `<span class="tab-meta">${escapeHtml(c.race)} ${escapeHtml(cls)}${c.level ? ` ${c.level}` : ""}${escapeHtml(sub)}</span>` +
        `${isPlayerTag}` +
        `</button>`
      );
    })
    .join("");

  sheetHost.innerHTML = chars.map(renderSheetView).join("");
  renderPartyOverview(chars);

  // Keep the previously selected tab if its character is still in the
  // party, otherwise default to the player (or first available).
  const stillActive = chars.find((c) => c.id === activeSheetId);
  const target = stillActive || chars[0];
  setActiveSheetTab(target.id);

  for (const btn of tabsHost.querySelectorAll(".char-tab")) {
    btn.addEventListener("click", () => setActiveSheetTab(btn.dataset.charId));
  }
  // Phase 39: inventory items open the inspection modal.
  for (const btn of sheetHost.querySelectorAll(".inv-item-btn")) {
    btn.addEventListener("click", () =>
      openItemModal(btn.dataset.charId, btn.dataset.itemName));
  }
  renderShopButtons();
  updateRollPreview();
  syncGameSessionState();
}

function setActiveSheetTab(charId) {
  activeSheetId = charId;
  const tabsHost = document.getElementById("char-tabs");
  const sheetHost = document.getElementById("sheet-host");
  if (!tabsHost || !sheetHost) return;
  for (const t of tabsHost.querySelectorAll(".char-tab")) {
    const selected = t.dataset.charId === charId;
    t.classList.toggle("active", selected);
    t.setAttribute("aria-selected", selected ? "true" : "false");
    t.tabIndex = selected ? 0 : -1;
  }
  for (const v of sheetHost.querySelectorAll(".sheet-view")) {
    const selected = v.dataset.charId === charId;
    v.classList.toggle("active", selected);
    v.hidden = !selected;
  }
  for (const card of document.querySelectorAll("[data-party-card]")) {
    card.classList.toggle("is-active", card.dataset.partyCard === charId);
  }
}

// Build the HTML for one character's tab content. Mirrors the player
// sheet (HP/CA/Prof/Speed + abilities + skills), adds conditions as
// colored pills, plus optional spells (casters only) and inventory
// sections — both rendered identically for the player and each
// companion so the table-tools tab view stays symmetric.
function renderSheetView(character) {
  const isPlayer = character.is_player ? " is-player" : "";
  const cls = character.class || "";
  const sub = character.subclass ? ` · ${character.subclass}` : "";
  const bg = character.background ? ` · ${character.background}` : "";
  const abilities = ABILITY_OPTIONS.map((a) => {
    const score = character.abilities?.[a.key] ?? 10;
    return `<div class="sheet-stat"><span>${a.short}</span><strong>${score}</strong><em>${fmtMod(scoreModifier(score))}</em></div>`;
  }).join("");
  const profSkills = (character.proficiencies?.skills || [])
    .map(skillLabel)
    .sort()
    .join(", ") || "nenhuma";
  const conditions = character.conditions || [];
  const conditionsHtml = conditions.length
    ? `<div class="sheet-meta sheet-conds">Condicoes: ${conditions
        .map((c) => `<span class="cond-pill">${escapeHtml(c)}</span>`)
        .join(" ")}</div>`
    : "";
  const spellsHtml = renderSpellsSection(character.spellcasting);
  const inventoryHtml = renderInventorySection(character);
  return (
    `<div id="sheet-${escapeHtml(character.id)}" class="sheet-view${isPlayer}" data-char-id="${escapeHtml(character.id)}" role="tabpanel">` +
      `<div class="sheet-head"><strong>${escapeHtml(character.name)}</strong>` +
      `<span>${escapeHtml(character.race)} ${escapeHtml(cls)}${character.level ? ` ${character.level}` : ""}${escapeHtml(sub)}${escapeHtml(bg)}</span></div>` +
      `<div class="sheet-meta">PV ${character.hp_current}/${character.hp_max}${character.temp_hp ? ` (+${character.temp_hp} temp)` : ""} · CA ${character.armor_class} · Prof ${fmtMod(character.proficiency_bonus || 0)} · Desl ${character.speed || 30} ft</div>` +
      `<div class="sheet-stats">${abilities}</div>` +
      `<div class="sheet-meta">Pericias: ${profSkills}</div>` +
      conditionsHtml +
      spellsHtml +
      inventoryHtml +
    `</div>`
  );
}

// Render the spells (CD, attack bonus, ability, concentration, ritual),
// cantrips, prepared/known spells (split by level), spellbook (Wizard),
// and slot table. Returns an empty string when ``sc`` is missing — so
// non-casters just show no magia section.
function renderSpellsSection(sc) {
  if (!sc) return "";
  const abilityName = abilityLabel(sc.ability || "");
  const parts = [];
  parts.push(
    `<strong>Magia:</strong> CD ${sc.save_dc ?? "—"} · ` +
    `Ataque ${fmtMod(sc.attack_bonus ?? 0)} · ${escapeHtml(abilityName)}` +
    (sc.concentration ? ` · Concentrando em <em>${escapeHtml(sc.concentration)}</em>` : "") +
    (sc.ritual_casting ? " · Ritual" : ""),
  );
  const cantrips = sc.cantrips_known || [];
  if (cantrips.length) {
    parts.push(`Truques: ${cantrips.map(formatSpellName).join(", ")}`);
  }
  // Prepared (Cleric/Druid/Paladin) takes precedence over known (Bard/Sorcerer/Warlock).
  const prepared = sc.spells_prepared || [];
  const known = sc.spells_known || [];
  if (prepared.length) {
    parts.push(`Preparadas (${prepared.length}): ${prepared.map(formatSpellName).join(", ")}`);
  } else if (known.length) {
    parts.push(`Conhecidas (${known.length}): ${known.map(formatSpellName).join(", ")}`);
  }
  const spellbook = sc.spellbook || [];
  if (spellbook.length) {
    parts.push(`Livro (${spellbook.length}): ${spellbook.map(formatSpellName).join(", ")}`);
  }
  const slotsMax = sc.spell_slots_max || {};
  const slotsCur = sc.spell_slots || {};
  const slotRows = Object.keys(slotsMax)
    .map((k) => Number(k))
    .filter((k) => k > 0 && (slotsMax[k] || 0) > 0)
    .sort((a, b) => a - b)
    .map((k) => `${k}º: ${slotsCur[k] ?? 0}/${slotsMax[k]}`)
    .join(" · ");
  if (slotRows) {
    parts.push(`Slots: ${slotRows}`);
  }
  return parts
    .map((p) => `<div class="sheet-meta sheet-spells">${p}</div>`)
    .join("");
}

// Phase 39: interactive inventory — gold, equipped summary, and each
// item as a button that opens the inspection modal (equip / attune /
// drop / sell). Quantity shows as ×N, magic items get a rarity dot,
// attuned items a ◈ marker.
function renderInventorySection(character) {
  const inventory = Array.isArray(character.inventory) ? character.inventory : [];
  const parts = [];
  parts.push(
    `<div class="sheet-meta sheet-gold"><strong>Ouro:</strong> ${fmtGold(character.gold_gp)} gp</div>`,
  );
  const equipped = character.equipped || {};
  const equippedBits = Object.keys(SLOT_LABELS)
    .filter((slot) => equipped[slot])
    .map((slot) =>
      `<span class="equip-pill">${escapeHtml(SLOT_LABELS[slot])}: ${escapeHtml(equipped[slot].name)}</span>`);
  if (equippedBits.length) {
    parts.push(
      `<div class="sheet-meta sheet-equip"><strong>Equipado:</strong> ${equippedBits.join(" ")}</div>`,
    );
  }
  if (inventory.length) {
    const attunedNames = character.attuned_items || [];
    const items = inventory.map((it) => {
      const nm = escapeHtml(it.name || "—");
      const qty = Number(it.quantity || 0);
      const qStr = qty > 1 ? ` <span class="inv-qty">×${qty}</span>` : "";
      const magic = it.rarity && it.rarity !== "common"
        ? ` <span class="inv-magic" title="${escapeHtml(it.rarity)}">●</span>`
        : "";
      const att = attunedNames.includes(it.name)
        ? ' <span class="inv-attuned" title="sintonizado">◈</span>'
        : "";
      return (
        `<button type="button" class="inv-item-btn" ` +
        `data-char-id="${escapeHtml(character.id)}" data-item-name="${escapeHtml(it.name)}">` +
        `${nm}${qStr}${magic}${att}</button>`
      );
    });
    parts.push(
      `<div class="sheet-meta sheet-inv"><strong>Inventario:</strong> ${items.join(" ")}</div>`,
    );
  }
  return parts.join("");
}

// Convert a spell slug ("magic-missile") or stored name ("fire bolt")
// into a Title Case display label ("Magic Missile" / "Fire Bolt").
// Stable for already-titled names.
function formatSpellName(s) {
  return escapeHtml(String(s || "")).replace(/(^|[\s-])\w/g, (c) => c.toUpperCase());
}

// ============================================================================
// Phase 39 — inventário interativo + loja
// ============================================================================
//
// Toda mutação passa pelas rotas de web/routes_inventory.py (engine
// autoritativa, sem LLM). A resposta traz o Character atualizado; o
// front substitui a entrada da party e re-renderiza as fichas via
// persistSave() (que também sincroniza o save no Postgres).

// Mirrors engine/inventory.py::RARITY_PRICE_GP (fallback when the item
// has no explicit value_gp) and SELL_RATE (0.5).
const RARITY_PRICE_GP = {
  common: 100, uncommon: 500, rare: 5000, very_rare: 50000, legendary: 500000,
};
const SLOT_LABELS = {
  main_hand: "Mão principal", off_hand: "Mão inábil", armor: "Armadura",
  amulet: "Amuleto", ring_1: "Anel 1", ring_2: "Anel 2",
  cloak: "Capa", boots: "Botas",
};

function fmtGold(v) {
  return Number(v || 0).toLocaleString("pt-BR", { maximumFractionDigits: 2 });
}

function listPrice(item) {
  if (Number(item.value_gp) > 0) return Number(item.value_gp);
  if (item.rarity && RARITY_PRICE_GP[item.rarity]) return RARITY_PRICE_GP[item.rarity];
  return 0;
}

function inferSlots(item) {
  if (item.weapon) return ["main_hand", "off_hand"];
  if (item.armor && item.armor.is_shield) return ["off_hand"];
  if (item.armor) return ["armor"];
  return ["amulet", "ring_1", "ring_2", "cloak", "boots"];
}

function getPartyCharacter(charId) {
  const st = currentGameState;
  if (!st || !Array.isArray(st.party)) return null;
  return st.party.find((c) => c.id === charId) || null;
}

// Replace the party entry with the server-updated character, then
// re-render the sheets and sync the save (persistSave does both).
async function applyCharacterUpdate(character) {
  if (!currentGameState || !Array.isArray(currentGameState.party) || !character) return;
  const idx = currentGameState.party.findIndex((c) => c.id === character.id);
  if (idx >= 0) currentGameState.party[idx] = character;
  await persistSave(currentGameState);
}

// --- Item inspection modal ---

let itemModalCtx = null; // { charId, itemName }

function closeItemModal() {
  closeDialog("item-modal");
  itemModalCtx = null;
}

function openItemModal(charId, itemName) {
  const ch = getPartyCharacter(charId);
  if (!ch) return;
  const item = (ch.inventory || []).find((i) => i.name === itemName);
  if (!item) return;
  itemModalCtx = { charId, itemName };
  document.getElementById("item-modal-title").textContent = item.name;

  const attuned = (ch.attuned_items || []).includes(item.name);
  const equippedSlots = Object.keys(SLOT_LABELS).filter(
    (s) => ch.equipped && ch.equipped[s] && ch.equipped[s].name === item.name,
  );
  const meta = [];
  if (item.type) meta.push(item.type);
  if (item.rarity) meta.push(item.rarity.replace("_", " "));
  if (item.magic_bonus) meta.push(`+${item.magic_bonus}`);
  if (item.requires_attunement) meta.push(attuned ? "sintonizado ◈" : "requer sintonização");
  if (Number(item.quantity) > 1) meta.push(`×${item.quantity}`);

  const lines = [];
  if (meta.length) lines.push(`<p class="hint">${meta.map(escapeHtml).join(" · ")}</p>`);
  if (item.weapon) {
    lines.push(
      `<p class="hint">Dano: ${escapeHtml(item.weapon.damage_dice)} ${escapeHtml(item.weapon.damage_type)}</p>`,
    );
  }
  if (item.armor) {
    lines.push(
      `<p class="hint">CA base: ${item.armor.base_ac}${item.armor.is_shield ? " (escudo: +2 CA)" : ""}</p>`,
    );
  }
  if (item.description) {
    lines.push(`<p class="item-desc">${escapeHtml(item.description)}</p>`);
  }
  document.getElementById("item-modal-body").innerHTML = lines.join("");

  const actions = document.getElementById("item-modal-actions");
  if (readOnlyMode) {
    actions.innerHTML = "";
    openDialog("item-modal");
    return;
  }
  const sellPrice = listPrice(item) * 0.5;
  const slotOpts = inferSlots(item)
    .map((s) => `<option value="${s}">${SLOT_LABELS[s]}</option>`)
    .join("");
  const parts = [];
  parts.push(
    `<div class="row item-equip-row"><select id="item-slot-select">${slotOpts}</select>` +
    `<button id="item-equip-btn">Equipar</button></div>`,
  );
  for (const s of equippedSlots) {
    parts.push(
      `<button class="secondary item-unequip-btn" data-slot="${s}">Desequipar (${SLOT_LABELS[s]})</button>`,
    );
  }
  if (item.requires_attunement) {
    parts.push(attuned
      ? '<button class="secondary" id="item-unattune-btn">Remover sintonia</button>'
      : '<button class="secondary" id="item-attune-btn">Sintonizar</button>');
  }
  parts.push('<button class="secondary" id="item-drop-btn">Soltar 1</button>');
  parts.push(
    `<button class="secondary" id="item-sell-btn">Vender 1 (${fmtGold(sellPrice)} gp)</button>`,
  );
  actions.innerHTML = parts.join(" ");

  document.getElementById("item-equip-btn").onclick = () =>
    itemAction("equip", {
      item_id: itemName,
      slot: document.getElementById("item-slot-select").value,
    });
  for (const b of actions.querySelectorAll(".item-unequip-btn")) {
    b.onclick = () => itemAction("unequip", { slot: b.dataset.slot });
  }
  const attuneBtn = document.getElementById("item-attune-btn");
  if (attuneBtn) attuneBtn.onclick = () => itemAction("attune", { item_id: itemName });
  const unattuneBtn = document.getElementById("item-unattune-btn");
  if (unattuneBtn) unattuneBtn.onclick = () => itemAction("unattune", { item_id: itemName });
  document.getElementById("item-drop-btn").onclick = async () => {
    if (await confirmAction(`Soltar 1× ${itemName}?`, {
      title: "Soltar item",
      confirmLabel: "Soltar item",
    })) {
      itemAction("drop", { item_id: itemName, quantity: 1 });
    }
  };
  document.getElementById("item-sell-btn").onclick = () =>
    itemAction("sell", { item_id: itemName, quantity: 1 });

  openDialog("item-modal");
}

async function itemAction(action, body) {
  if (!currentSessionId || !itemModalCtx) return;
  const { charId, itemName } = itemModalCtx;
  try {
    const res = await api(
      `/api/sessions/${currentSessionId}/inventory/${action}`,
      { method: "POST", body: { ...body, character_id: charId } },
    );
    closeItemModal();
    for (const w of (res.result && res.result.warnings) || []) {
      appendLog("Sistema", w, "system");
    }
    if (res.result && res.result.ac_delta) {
      appendLog(
        "Sistema",
        `CA de ${res.character.name}: ${res.result.ac_before} → ${res.result.ac_after}.`,
        "system",
      );
    }
    if (action === "sell") {
      appendLog(
        "Sistema",
        `${res.character.name} vendeu ${itemName} (ouro: ${fmtGold(res.result.gold_gp)} gp).`,
        "system",
      );
    }
    await applyCharacterUpdate(res.character);
  } catch (e) {
    appendLog("Erro", e.message, "system");
  }
}

// --- Shop overlay ---

let shopVendorId = null;

function closeShopModal() {
  closeDialog("shop-modal");
  shopVendorId = null;
}

// One "Loja" button per vendor NPC currently in the state. The DM can
// flag vendors mid-game; the strip refreshes on every state update.
function renderShopButtons() {
  const host = document.getElementById("shop-buttons");
  if (!host) return;
  const npcs = (currentGameState && currentGameState.npcs) || [];
  const vendors = npcs.filter((n) => n.vendor && (n.shop_inventory || []).length);
  if (readOnlyMode || vendors.length === 0) {
    host.style.display = "none";
    host.innerHTML = "";
    return;
  }
  host.style.display = "";
  host.innerHTML = vendors
    .map((v) =>
      `<button type="button" class="secondary shop-open-btn" data-vendor-id="${escapeHtml(v.id)}">🛒 Loja: ${escapeHtml(v.name)}</button>`)
    .join(" ");
  for (const btn of host.querySelectorAll(".shop-open-btn")) {
    btn.addEventListener("click", () => openShop(btn.dataset.vendorId));
  }
}

async function openShop(vendorId) {
  if (!currentSessionId) return;
  try {
    const res = await api(
      `/api/sessions/${currentSessionId}/shop/${encodeURIComponent(vendorId)}`,
    );
    shopVendorId = vendorId;
    renderShopModal(res);
    openDialog("shop-modal");
  } catch (e) {
    appendLog("Erro", "Não foi possível abrir a loja: " + e.message, "system");
  }
}

function renderShopModal(data) {
  document.getElementById("shop-modal-title").textContent = `Loja — ${data.vendor_name}`;
  document.getElementById("shop-gold").textContent = `Seu ouro: ${fmtGold(data.gold_gp)} gp`;
  const host = document.getElementById("shop-stock");
  const rows = (data.stock || []).map((s) => {
    const known = !!s.item;
    const name = known ? s.item.name : s.item_id;
    const afford = known && Number(data.gold_gp) >= Number(s.price_gp);
    const magic = known && s.item.rarity && s.item.rarity !== "common"
      ? ` <span class="inv-magic" title="${escapeHtml(s.item.rarity)}">●</span>`
      : "";
    return (
      '<div class="shop-row">' +
      `<span class="shop-name">${escapeHtml(name)}${magic}</span>` +
      `<span class="shop-price">${fmtGold(s.price_gp)} gp</span>` +
      `<button class="shop-buy-btn" data-item-id="${escapeHtml(s.item_id)}"${afford ? "" : " disabled"}>Comprar</button>` +
      "</div>"
    );
  });
  host.innerHTML = rows.join("") || '<p class="hint">Sem estoque.</p>';
  for (const b of host.querySelectorAll(".shop-buy-btn")) {
    b.addEventListener("click", () => buyFromShop(b.dataset.itemId));
  }
}

async function buyFromShop(itemId) {
  if (!currentSessionId || !shopVendorId) return;
  const vendorId = shopVendorId;
  try {
    const res = await api(`/api/sessions/${currentSessionId}/inventory/buy`, {
      method: "POST",
      body: { vendor_id: vendorId, item_id: itemId, quantity: 1 },
    });
    appendLog(
      "Sistema",
      `Comprou ${itemId} (ouro: ${fmtGold(res.result.gold_gp)} gp).`,
      "system",
    );
    await applyCharacterUpdate(res.character);
    await openShop(vendorId); // refresh stock affordances with new gold
  } catch (e) {
    if (e && e.status === 402) {
      appendLog("Sistema", "Ouro insuficiente para essa compra.", "system");
      return;
    }
    appendLog("Erro", e.message, "system");
  }
}

function parseRollSelection(value) {
  const [kind, key] = String(value || "").split(":");
  return { kind, key };
}

function rollLabel(kind, key) {
  if (kind === "skill") return skillLabel(key);
  if (kind === "save") return `Salvaguarda de ${abilityLabel(key)}`;
  return `Teste de ${abilityLabel(key)}`;
}

function updateRollPreview() {
  const preview = document.getElementById("roll-preview");
  const select = document.getElementById("roll-check");
  const btn = document.getElementById("roll-btn");
  if (!preview || !select) return;
  const character = getPlayerCharacter();
  const { kind, key } = parseRollSelection(select.value);
  const parts = modifierForSelection(character, kind, key);
  if (!character || !parts) {
    preview.textContent = "Sem ficha de jogador nesta sessao.";
    if (btn) btn.disabled = true;
    return;
  }
  if (btn) btn.disabled = readOnlyMode;
  const profText = parts.proficient
    ? ` + prof ${fmtMod(parts.profBonus)}`
    : " + sem prof";
  preview.textContent =
    `${rollLabel(kind, key)}: ${abilityShort(parts.ability)} ${fmtMod(parts.abilityMod)}${profText} = ${fmtMod(parts.total)}`;
}

function describeRollResult(r) {
  const dice = r.rolls.length > 1
    ? `[${r.rolls.join(", ")}] fica ${r.natural}`
    : `${r.natural}`;
  const mode = r.advantage ? " com vantagem" : r.disadvantage ? " com desvantagem" : "";
  const prof = r.proficient
    ? `, proficiencia ${fmtMod(r.proficiency_bonus)}`
    : ", sem proficiencia";
  return `${r.character_name} rolou ${r.label}${mode}: d20 ${dice} ${fmtMod(r.modifier)} = ${r.total} (${abilityLabel(r.ability)} ${fmtMod(r.ability_modifier)}${prof}).`;
}

async function rollCheck(check, kind = null, advantage = false, disadvantage = false) {
  if (busy || readOnlyMode || !currentSessionId) return;
  lockUi();
  try {
    const res = await api(`/api/sessions/${currentSessionId}/roll-check`, {
      method: "POST",
      body: { check, kind, advantage, disadvantage },
    });
    appendLog("Dados", describeRollResult(res), "system");
  } catch (e) {
    if (e && (e.status === 401 || e.status === 404)) {
      sessionExpired = true;
      syncGameSessionState();
      appendLog("Sistema", "Esta sessão expirou. Volte às campanhas para continuar.", "system");
      return;
    }
    appendLog("Erro", "Nao foi possivel rolar: " + e.message, "system");
  } finally {
    unlockUi();
    updateRollPreview();
  }
}

async function rollSelectedCheck() {
  const select = document.getElementById("roll-check");
  if (!select) return;
  const { kind, key } = parseRollSelection(select.value);
  const adv = !!document.getElementById("roll-adv")?.checked;
  const dis = !!document.getElementById("roll-dis")?.checked;
  await rollCheck(key, kind, adv, dis);
}

async function sendInput() {
  if (busy || readOnlyMode) return;
  const input = document.getElementById("cmd");
  const line = input.value.trim();
  if (!line || !currentSessionId) return;
  input.value = "";
  appendLog("Você", line, "player");
  // Meta-commands (/quit /save /load /list /help) are handled locally
  // and never reach the backend. clientCommand returns true when it
  // consumed the line; otherwise we fall through and send it as input.
  if (line.startsWith("/")) {
    const handled = await clientCommand(line);
    if (handled) return;
  }
  lockUi();
  showTyping();
  try {
    await sendInputClassic(line);
  } finally {
    unlockUi();
    hideTyping();
  }
}

async function sendInputClassic(line) {
  try {
    const res = await api(`/api/sessions/${currentSessionId}/input`, {
      method: "POST",
      body: { line },
    });
    if (res.state) {
      currentGameState = res.state;
      renderCharacterTools();
      updateLevelupBanner();  // Phase 38
      checkPendingASI();  // Phase 38 — combat-end may have queued an ASI
      checkPendingReaction();  // Phase 41c — enemy hit/spell may open a reaction
      await persistSave(res.state);
    }
    const r = res.result || {};
    if (r.error) {
      appendLog("Erro", r.error, "system");
    } else {
      if (r.narration) {
        appendLog("DM", r.narration, "narration");
        lastDmNarration = r.narration;  // Phase 42 — 🔊 replay + auto-TTS
        refreshAudioButtons();
        maybeAutoPlayTTS(r.narration);
      }
      if (r.action_result) {
        const ar = typeof r.action_result === "string"
          ? r.action_result
          : JSON.stringify(r.action_result);
        appendLog("Ação", ar, "system");
      }
      if (r.companion_results && r.companion_results.length) {
        for (const c of r.companion_results) {
          const who = c.character_name || "Companheiro";
          const body = (c.narration || c.action || "").trim();
          if (body) appendLog(who, body, "companion");
        }
      }
    }
  } catch (e) {
    if (e && e.status === 429) {
      quotaReached = true;
      syncGameSessionState();
      appendLog("Sistema", LIMIT_REACHED_MSG, "system");
      return;
    }
    if (e && (e.status === 401 || e.status === 404)) {
      sessionExpired = true;
      syncGameSessionState();
      appendLog("Sistema", "Esta sessão expirou. Volte às campanhas para continuar.", "system");
      return;
    }
    appendLog("Erro", e.message, "system");
  }
}

// Leave the current game session and return to the saves list.
// The active session stays in Redis (auto-saved on every input); the
// persistent save is the source of truth, so dropping the in-memory
// session id here is safe — the game can be reloaded from the lobby.
function returnToLobby() {
  appendLog("Sistema", "Voltando ao lobby...", "system");
  currentSessionId = null;
  currentGameState = null;
  readOnlyMode = false;
  quotaReached = false;
  sessionExpired = false;
  selectGamePanel("narrative", { focus: false });
  loadLobby();
}

function openPlayGuide() {
  selectGamePanel("commands");
}

function fillCommand(command) {
  const input = document.getElementById("cmd");
  if (!input || input.disabled) return;
  input.value = command;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

function selectGamePanel(panel, { focus = true } = {}) {
  const tools = document.getElementById("table-tools");
  const unavailableTool = ["party", "roll"].includes(panel) && tools?.style.display === "none";
  const selected = !unavailableTool && ["party", "roll", "commands"].includes(panel)
    ? panel
    : "narrative";
  const guide = document.getElementById("play-guide");
  const title = document.getElementById("game-tools-title");

  if (guide) guide.open = selected === "commands";
  if (tools) {
    tools.classList.toggle("is-mobile-open", selected === "party" || selected === "roll");
    tools.dataset.mobilePanel = selected === "roll" ? "roll" : "party";
  }
  if (title) title.textContent = selected === "roll" ? "Dados e testes" : "Grupo e ficha";

  for (const tab of document.querySelectorAll(".game-mobile-tab")) {
    const active = tab.dataset.gamePanel === selected;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  }

  if (!focus) return;
  if (selected === "party") {
    tools?.querySelector("[data-open-sheet], .char-tab")?.focus();
  } else if (selected === "roll") {
    document.getElementById("roll-check")?.focus();
  } else if (selected === "commands") {
    guide?.querySelector("summary")?.focus();
  } else {
    const input = document.getElementById("cmd");
    if (input && !input.disabled) input.focus();
    else document.getElementById("output")?.focus();
  }
}

// --- /command helpers (client-side) ---
async function clientCommand(line) {
  // /quit, /save, /load, /list, /help — handled locally.
  const parts = line.trim().split(/\s+/);
  const cmd = parts[0].toLowerCase();
  if (cmd === "/help") {
    openPlayGuide();
    appendLog("Sistema",
      "Guia aberto abaixo do campo de comando. Use-o para ver quando usar cada comando.",
      "system");
    return true;
  }
  if (cmd === "/roll" || cmd === "/teste" || cmd === "/check") {
    let kind = null;
    let checkParts = parts.slice(1);
    const first = (checkParts[0] || "").toLowerCase();
    if (["skill", "pericia"].includes(first)) {
      kind = "skill";
      checkParts = checkParts.slice(1);
    } else if (["ability", "atributo", "habilidade"].includes(first)) {
      kind = "ability";
      checkParts = checkParts.slice(1);
    } else if (["save", "salvaguarda", "resistencia"].includes(first)) {
      kind = "save";
      checkParts = checkParts.slice(1);
    }
    const check = checkParts.join(" ");
    if (!check) {
      appendLog("Erro", "Uso: /roll furtividade ou /roll save destreza", "system");
      return true;
    }
    await rollCheck(check, kind);
    return true;
  }
  if (cmd === "/quit") {
    returnToLobby();
    return true;
  }
  if (cmd === "/save") {
    const slug = parts[1] || currentSlug || "default";
    try {
      const s = await api(`/api/sessions/${currentSessionId}`);
      await api("/api/saves", {
        method: "POST",
        body: { slug, state: s.state },
      });
      currentSlug = slug;
      appendLog("Sistema", `Jogo salvo como ${slug}.`, "system");
    } catch (e) {
      appendLog("Erro", e.message, "system");
    }
    return true;
  }
  if (cmd === "/load") {
    const slug = parts[1];
    if (!slug) {
      appendLog("Erro", "Uso: /load <slug>", "system");
      return true;
    }
    await loadSaveAsSession(slug);
    return true;
  }
  if (cmd === "/list") {
    try {
      const saves = await api("/api/saves");
      if (saves.length === 0) {
        appendLog("Sistema", "(nenhum save)", "system");
      } else {
        for (const s of saves) {
          appendLog("Sistema", `  ${s.slug} (${s.updated_at})`, "system");
        }
      }
    } catch (e) {
      appendLog("Erro", e.message, "system");
    }
    return true;
  }
  if (cmd === "/award-xp" || cmd === "/award") {
    // Phase 38 — manually credit XP to the party pool. Usage:
    //   /award-xp 500
    // The backend (/api/sessions/{sid}/award-xp) runs award_party_xp
    // and may auto-level every party member. If the player gets a
    // queued ASI we open the modal automatically.
    const amount = parseInt(parts[1], 10);
    if (!Number.isFinite(amount) || amount <= 0) {
      appendLog("Erro", "Uso: /award-xp <quantidade>", "system");
      return true;
    }
    await awardXP(amount);
    return true;
  }
  return false;
}

// --- Phase 42: TTS + ambient music ---
// Preferences live on the user object (`user.preferences`). TTS synthesis hits
// /api/tts/speak (disk-cached server-side); the in-memory Map below caches the
// blob URLs client-side so replaying the same narration is instant. Ambient
// music is a plain <audio loop> fed a user-supplied URL.
let lastDmNarration = "";
let audioUnlocked = false;  // set on first user gesture (iOS Safari gate)
const ttsBlobCache = new Map();  // key `${text}|${voice}|${rate}` -> objectURL
let musicVolume = 0.4;

const DEFAULT_PREFS = {
  tts: { enabled: false, voice: "", rate: "+0%", auto_play: false },
  music: { enabled: false, src: "", volume: 0.4 },
};

function prefs() {
  const u = getUser();
  const stored = (u && u.preferences) || {};
  const merged = {};
  for (const section of Object.keys(DEFAULT_PREFS)) {
    merged[section] = { ...DEFAULT_PREFS[section], ...(stored[section] || {}) };
  }
  return merged;
}

function ttsPlayer() {
  return document.getElementById("tts-player");
}
function musicPlayer() {
  return document.getElementById("music-player");
}

function online() {
  return typeof navigator !== "undefined" ? navigator.onLine : true;
}

function refreshAudioButtons() {
  const ttsBtn = document.getElementById("tts-btn");
  const musicBtn = document.getElementById("music-btn");
  const p = prefs();
  if (ttsBtn) {
    ttsBtn.disabled = !online() || readOnlyMode || !lastDmNarration;
  }
  if (musicBtn) {
    musicBtn.disabled = !online() || !(p.music.enabled && p.music.src);
    musicBtn.classList.toggle("active", !musicPlayer().paused && !musicBtn.disabled);
  }
}

async function playTTS(text) {
  if (!text || !online()) return;
  const p = prefs();
  if (!p.tts.enabled) return;
  audioUnlocked = true;
  const voice = p.tts.voice || "";
  const rate = p.tts.rate || "+0%";
  const key = `${text}|${voice}|${rate}`;
  const player = ttsPlayer();
  const ttsBtn = document.getElementById("tts-btn");
  try {
    if (ttsBtn) ttsBtn.disabled = true;
    let url = ttsBlobCache.get(key);
    if (!url) {
      const params = new URLSearchParams({ text, voice, rate });
      const tok = getToken();
      const resp = await fetch(`${API_BASE}/api/tts/speak?${params}`, {
        headers: tok ? { Authorization: `Bearer ${tok}` } : {},
      });
      if (resp.status === 503) {
        appendLog("Sistema", "Serviço de voz indisponível agora.", "system");
        return;
      }
      if (!resp.ok) return;
      const blob = await resp.blob();
      url = URL.createObjectURL(blob);
      ttsBlobCache.set(key, url);
    }
    player.src = url;
    await player.play();
  } catch (e) {
    // Autoplay rejection (no gesture yet) — silently ignore; the 🔊 button
    // still works because it counts as a gesture.
  } finally {
    refreshAudioButtons();
  }
}

function maybeAutoPlayTTS(text) {
  const p = prefs();
  if (p.tts.enabled && p.tts.auto_play && audioUnlocked && online()) {
    playTTS(text);
  }
}

function initMusicPlayer() {
  const p = prefs();
  const player = musicPlayer();
  musicVolume = p.music.volume ?? 0.4;
  const volEl = document.getElementById("music-volume");
  if (volEl) volEl.value = String(musicVolume);
  if (p.music.enabled && p.music.src && online()) {
    player.src = p.music.src;
    player.volume = musicVolume;
    // Best-effort autoplay; browsers may block until a gesture.
    player.play().catch(() => {});
  } else {
    player.removeAttribute("src");
    player.load();
  }
  refreshAudioButtons();
}

function toggleMusic() {
  const player = musicPlayer();
  const p = prefs();
  if (!(p.music.enabled && p.music.src)) return;
  audioUnlocked = true;
  if (player.paused) {
    player.volume = musicVolume;
    player.play().catch(() => {});
  } else {
    player.pause();
  }
  refreshAudioButtons();
}

// --- Phase 42: Voz & Música settings modal ---
async function openPrefsModal() {
  const p = prefs();
  document.getElementById("prefs-tts-enabled").checked = !!p.tts.enabled;
  document.getElementById("prefs-tts-auto").checked = !!p.tts.auto_play;
  document.getElementById("prefs-tts-rate").value = p.tts.rate || "+0%";
  document.getElementById("prefs-music-enabled").checked = !!p.music.enabled;
  document.getElementById("prefs-music-src").value = p.music.src || "";
  document.getElementById("prefs-music-volume").value = String(p.music.volume ?? 0.4);
  document.getElementById("prefs-volume-output").value =
    `${Math.round((p.music.volume ?? 0.4) * 100)}%`;
  const user = getUser();
  document.getElementById("prefs-account-username").textContent = user?.username || "—";
  document.getElementById("prefs-account-role").textContent =
    user?.role === "admin" ? "Administrador" : "Jogador";
  selectPrefsTab("narration", { focus: false });
  setMsg("prefs-msg", "", "");
  openDialog("prefs-modal", { initialFocus: "#prefs-tts-enabled" });

  // Lazy-load pt-* voices the first time the modal opens.
  const sel = document.getElementById("prefs-tts-voice");
  if (sel.options.length <= 1) {
    try {
      const data = await api("/api/tts/voices");
      for (const v of data.voices) {
        const name = v.ShortName || v.Name || "";
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = `${name} (${v.Gender || ""})`.trim();
        sel.appendChild(opt);
      }
    } catch (_) {
      // Voices unavailable (503) — leave the "(padrão)" option in place.
    }
  }
  sel.value = p.tts.voice || "";
}

function closePrefsModal() {
  closeDialog("prefs-modal");
}

function selectPrefsTab(tab, { focus = true } = {}) {
  const buttons = [...document.querySelectorAll("[data-prefs-tab]")];
  for (const button of buttons) {
    const selected = button.dataset.prefsTab === tab;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(`prefs-panel-${button.dataset.prefsTab}`);
    if (panel) panel.hidden = !selected;
    if (selected && focus) button.focus();
  }
}

async function persistPrefs(patch) {
  const merged = await api("/api/me/preferences", { method: "PATCH", body: patch });
  const u = getUser();
  if (u) {
    u.preferences = merged;
    setUser(u);
  }
  return merged;
}

// Debounced PATCH for slider/checkbox changes inside the modal.
let prefsSaveTimer = null;
function schedulePrefsSave() {
  clearTimeout(prefsSaveTimer);
  setMsg("prefs-msg", "Salvando…", "");
  prefsSaveTimer = setTimeout(() => {
    const patch = {
      tts: {
        enabled: document.getElementById("prefs-tts-enabled").checked,
        auto_play: document.getElementById("prefs-tts-auto").checked,
        voice: document.getElementById("prefs-tts-voice").value,
        rate: document.getElementById("prefs-tts-rate").value,
      },
      music: {
        enabled: document.getElementById("prefs-music-enabled").checked,
        src: document.getElementById("prefs-music-src").value.trim(),
        volume: parseFloat(document.getElementById("prefs-music-volume").value),
      },
    };
    persistPrefs(patch)
      .then(() => {
        initMusicPlayer();
        refreshAudioButtons();
        setMsg("prefs-msg", "Preferências salvas.", "ok");
      })
      .catch((e) => setMsg("prefs-msg", "Erro: " + e.message, "error"));
  }, 400);
}

// --- Wire up events ---
document.addEventListener("DOMContentLoaded", () => {
  initShell();
  const authForm = document.getElementById("auth-form");
  authForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!authForm.reportValidity()) return;
    if (authMode === "signup") doSignup();
    else doLogin();
  });
  document.getElementById("auth-mode-login").onclick = () => setAuthMode("login");
  document.getElementById("auth-mode-signup").onclick = () => setAuthMode("signup");
  document.getElementById("auth-close").onclick = () => closeAuthDialog();
  document.querySelector("[data-auth-close]").onclick = () => closeAuthDialog();
  for (const id of ["nav-login", "hero-login"]) {
    document.getElementById(id).onclick = () => openAuthDialog("login");
  }
  for (const id of ["nav-signup", "hero-signup", "footer-signup"]) {
    document.getElementById(id).onclick = () => openAuthDialog("signup");
  }
  document.getElementById("logout-btn").onclick = doLogout;
  document.getElementById("shell-lobby-btn").onclick = returnToLobby;
  document.getElementById("shell-game-btn").onclick = () => show("game-screen");
  document.getElementById("shell-admin-btn").onclick = openAdminPanel;
  document.getElementById("new-game-btn").onclick = createEmptySession;
  document.getElementById("wizard-btn").onclick = openWizard;
  document.getElementById("empty-wizard-btn").onclick = openWizard;
  document.getElementById("lobby-active-tab").onclick = () => loadLobbyTab("active");
  document.getElementById("lobby-archived-tab").onclick = () => loadLobbyTab("archived");
  document.getElementById("lobby-retry").onclick = () => loadLobbyTab(lobbyTab);
  document.querySelector(".lobby-tabs").addEventListener("keydown", (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const next = lobbyTab === "active" ? "archived" : "active";
    const nextTab = document.getElementById(
      next === "active" ? "lobby-active-tab" : "lobby-archived-tab",
    );
    nextTab.focus();
    loadLobbyTab(next);
  });
  document.getElementById("send-btn").onclick = sendInput;
  document.querySelectorAll(".command-chip, .game-quick-action[data-command]").forEach((btn) => {
    btn.addEventListener("click", () => fillCommand(btn.dataset.command || ""));
  });
  document.querySelectorAll(".game-mobile-tab").forEach((btn) => {
    btn.addEventListener("click", () => selectGamePanel(btn.dataset.gamePanel));
  });
  document.getElementById("game-tools-close")?.addEventListener(
    "click", () => selectGamePanel("narrative"),
  );
  document.getElementById("game-guide-close")?.addEventListener(
    "click", () => selectGamePanel("narrative"),
  );
  document.getElementById("game-commands-btn")?.addEventListener(
    "click", () => selectGamePanel("commands"),
  );
  document.getElementById("play-guide")?.addEventListener("toggle", (event) => {
    if (!event.currentTarget.open) selectGamePanel("narrative", { focus: false });
  });
  const rollBtn = document.getElementById("roll-btn");
  if (rollBtn) rollBtn.onclick = rollSelectedCheck;
  const rollCheck = document.getElementById("roll-check");
  if (rollCheck) rollCheck.onchange = updateRollPreview;
  const rollAdv = document.getElementById("roll-adv");
  const rollDis = document.getElementById("roll-dis");
  if (rollAdv) {
    rollAdv.onchange = () => {
      if (rollAdv.checked && rollDis) rollDis.checked = false;
    };
  }
  if (rollDis) {
    rollDis.onchange = () => {
      if (rollDis.checked && rollAdv) rollAdv.checked = false;
    };
  }
  // Admin panel (Phase 30).
  const adminCreate = document.getElementById("admin-create-btn");
  if (adminCreate) adminCreate.onclick = openCreateUserModal;
  const adminRefresh = document.getElementById("admin-refresh-btn");
  if (adminRefresh) adminRefresh.onclick = loadAdminPanel;
  const adminQ = document.getElementById("admin-q");
  if (adminQ) adminQ.oninput = renderAdminUsers;
  for (const id of ["admin-status-filter", "admin-role-filter"]) {
    document.getElementById(id)?.addEventListener("change", renderAdminUsers);
  }
  document.querySelectorAll("[data-admin-sort]").forEach((button) => {
    button.addEventListener("click", () => changeAdminSort(button.dataset.adminSort));
  });
  const adminModalClose = document.getElementById("admin-modal-close");
  if (adminModalClose) adminModalClose.onclick = closeAdminModal;
  const adminDetailClose = document.getElementById("admin-detail-close");
  if (adminDetailClose) adminDetailClose.onclick = closeAdminDetail;
  document.querySelectorAll("[data-prefs-tab]").forEach((button) => {
    button.addEventListener("click", () => selectPrefsTab(button.dataset.prefsTab));
  });
  document.querySelector(".prefs-tabs")?.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll("[data-prefs-tab]")];
    const current = tabs.indexOf(document.activeElement);
    let next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    selectPrefsTab(tabs[next].dataset.prefsTab);
  });
  document.getElementById("prefs-music-volume")?.addEventListener("input", (event) => {
    document.getElementById("prefs-volume-output").value =
      `${Math.round(Number(event.currentTarget.value) * 100)}%`;
  });
  document.getElementById("prefs-logout")?.addEventListener("click", () => {
    closePrefsModal();
    doLogout();
  });
  document.getElementById("wz-prev").onclick = wizardPrev;
  document.getElementById("wz-next").onclick = wizardNext;
  document.getElementById("wz-finish").onclick = wizardFinish;
  document.getElementById("wizard-retry").onclick = openWizard;
  // Phase 35: ✨ AI name suggestions in the wizard's first step.
  document.getElementById("wz-campaign-name-ai").onclick = () =>
    suggestWizardName("campaign");
  document.getElementById("wz-char-name-ai").onclick = () =>
    suggestWizardName("character");
  // Phase 38 — ASI modal handlers.
  const asiClose = document.getElementById("asi-modal-close");
  if (asiClose) asiClose.onclick = closeASIModal;
  const asiConfirm = document.getElementById("asi-confirm");
  if (asiConfirm) asiConfirm.onclick = confirmASI;
  // Phase 39 — item + shop modal handlers.
  const itemClose = document.getElementById("item-modal-close");
  if (itemClose) itemClose.onclick = closeItemModal;
  const shopClose = document.getElementById("shop-modal-close");
  if (shopClose) shopClose.onclick = closeShopModal;
  // Phase 41c — reaction modal handlers.
  const rxClose = document.getElementById("reaction-close");
  if (rxClose) rxClose.onclick = () => closeReactionModal(/*decline*/ true);
  const rxPass = document.getElementById("reaction-pass");
  if (rxPass) rxPass.onclick = () => closeReactionModal(/*decline*/ true);
  // Phase 42 — TTS + ambient music.
  const prefsBtn = document.getElementById("prefs-btn");
  if (prefsBtn) prefsBtn.onclick = openPrefsModal;
  const prefsClose = document.getElementById("prefs-close");
  if (prefsClose) prefsClose.onclick = closePrefsModal;
  const ttsBtn = document.getElementById("tts-btn");
  if (ttsBtn) ttsBtn.onclick = () => playTTS(lastDmNarration);
  const musicBtn = document.getElementById("music-btn");
  if (musicBtn) musicBtn.onclick = toggleMusic;
  const musicVol = document.getElementById("music-volume");
  if (musicVol) {
    musicVol.oninput = () => {
      musicVolume = parseFloat(musicVol.value);
      musicPlayer().volume = musicVolume;
    };
  }
  const ttsTest = document.getElementById("prefs-tts-test");
  if (ttsTest) {
    ttsTest.onclick = () =>
      playTTS("Esta é uma amostra da narração em voz do mestre.");
  }
  // Persist prefs on any change inside the modal (debounced PATCH).
  ["prefs-tts-enabled", "prefs-tts-auto", "prefs-tts-voice", "prefs-tts-rate",
   "prefs-music-enabled", "prefs-music-src", "prefs-music-volume"].forEach(
    (id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("change", schedulePrefsSave);
      if (el && el.id === "prefs-music-src") el.addEventListener("input", schedulePrefsSave);
    },
  );
  // Refresh audio button state when connectivity changes.
  window.addEventListener("online", () => {
    refreshAudioButtons();
    syncGameSessionState();
  });
  window.addEventListener("offline", () => {
    refreshAudioButtons();
    syncGameSessionState();
  });
  const cmd = document.getElementById("cmd");
  cmd.addEventListener("keydown", (e) => {
    if (busy) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendInput();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || document.body.dataset.screen !== "game-screen") return;
    const toolsOpen = document.getElementById("table-tools")?.classList.contains("is-mobile-open");
    const guideOpen = document.getElementById("play-guide")?.open;
    if (toolsOpen || guideOpen) selectGamePanel("narrative");
  });

  // Auto-login if token present.
  if (getToken()) {
    api("/api/auth/me")
      .then((u) => {
        setUser(u);
        afterLogin();
      })
      .catch(() => {
        setToken(null);
        setUser(null);
        show("auth-screen");
      });
  } else {
    show("auth-screen");
  }
});


// ============================================================================
// Admin panel (Phase 30) — user management, limits, cost, activity log
// ============================================================================

let adminUsers = []; // cache of GET /api/admin/users for client-side filtering
let adminSort = { key: "username", direction: "asc" };

async function openAdminPanel() {
  if (!isAdmin()) return;
  show("admin-panel-screen");
  await loadAdminPanel();
}

async function loadAdminPanel() {
  setMsg("admin-msg", "Carregando...", "");
  try {
    const [users, summary] = await Promise.all([
      api("/api/admin/users"),
      api("/api/admin/usage/summary"),
    ]);
    adminUsers = users || [];
    renderAdminSummary(summary || {});
    renderAdminUsers();
    setMsg("admin-msg", "", "");
  } catch (e) {
    setMsg("admin-msg", "Erro ao carregar painel: " + e.message, "error");
  }
}

function renderAdminSummary(s) {
  const root = document.getElementById("admin-summary");
  if (!root) return;
  const cards = [
    { label: "Custo (mês)", value: `$${Number(s.cost_usd || 0).toFixed(4)}` },
    { label: "Tokens (mês)", value: Number(s.tokens || 0).toLocaleString("pt-BR") },
    { label: "Usuários ativos", value: String(s.active_users ?? 0) },
    { label: "Desativados", value: String(s.disabled_users ?? 0) },
  ];
  root.innerHTML = cards.map(
    (item) => `<div><dt>${item.label}</dt><dd>${item.value}</dd></div>`,
  ).join("");
}

function adminStatusValue(user) {
  if (!user.active) return "disabled";
  return user.unlimited ? "unlimited" : "active";
}

function changeAdminSort(key) {
  adminSort = {
    key,
    direction: adminSort.key === key && adminSort.direction === "asc" ? "desc" : "asc",
  };
  document.querySelectorAll("[data-admin-sort]").forEach((button) => {
    const active = button.dataset.adminSort === key;
    button.classList.toggle("active", active);
    if (active) button.parentElement.setAttribute("aria-sort", adminSort.direction === "asc" ? "ascending" : "descending");
    else button.parentElement.removeAttribute("aria-sort");
    const indicator = button.querySelector("span");
    if (indicator) indicator.textContent = active ? (adminSort.direction === "asc" ? "↑" : "↓") : "↕";
  });
  renderAdminUsers();
}

function renderAdminUsers() {
  const tbody = document.getElementById("admin-users-tbody");
  if (!tbody) return;
  const q = (document.getElementById("admin-q")?.value || "")
    .toLowerCase()
    .trim();
  const statusFilter = document.getElementById("admin-status-filter")?.value || "all";
  const roleFilter = document.getElementById("admin-role-filter")?.value || "all";
  const rows = adminUsers.filter((u) =>
    (!q || u.username.toLowerCase().includes(q)) &&
    (statusFilter === "all" || adminStatusValue(u) === statusFilter) &&
    (roleFilter === "all" || u.role === roleFilter),
  ).sort((a, b) => {
    const key = adminSort.key;
    const left = key === "status" ? adminStatusValue(a) : a[key];
    const right = key === "status" ? adminStatusValue(b) : b[key];
    const result = typeof left === "string"
      ? left.localeCompare(String(right), "pt-BR", { sensitivity: "base" })
      : Number(left || 0) - Number(right || 0);
    return adminSort.direction === "asc" ? result : -result;
  });
  tbody.innerHTML = "";
  for (const u of rows) {
    const tr = document.createElement("tr");
    const status = !u.active
      ? '<span class="tag tag-off">desativado</span>'
      : u.unlimited
        ? '<span class="tag tag-unlim">ilimitado</span>'
        : '<span class="tag tag-on">ativo</span>';
    const limit = u.unlimited
      ? "∞"
      : u.daily_token_limit != null
        ? u.daily_token_limit.toLocaleString("pt-BR")
        : "(padrão)";
    tr.innerHTML =
      `<td data-label="Usuário"><strong>${escapeHtml(u.username)}</strong><br><span class="meta">id ${u.id}</span></td>` +
      `<td data-label="Papel">${u.role === "admin" ? "Administrador" : "Jogador"}</td>` +
      `<td data-label="Status">${status}</td>` +
      `<td data-label="Uso hoje"><span class="admin-number">${u.tokens_today.toLocaleString("pt-BR")}</span> / ${limit}</td>` +
      `<td data-label="Custo no mês" class="admin-number">$${Number(u.cost_month || 0).toFixed(4)}</td>` +
      `<td data-label="Ações" class="admin-actions"></td>`;
    const actions = tr.querySelector(".admin-actions");
    actions.appendChild(adminMiniBtn("Editar", () => openEditUserModal(u)));
    actions.appendChild(adminMiniBtn("Senha", () => openResetPasswordModal(u)));
    actions.appendChild(
      adminMiniBtn(u.active ? "Desativar" : "Reativar", () => adminToggleActive(u)),
    );
    actions.appendChild(adminMiniBtn("Detalhes", () => loadUserDetail(u.id, u.username)));
    actions.appendChild(
      adminMiniBtn("Excluir", () => adminDeleteUser(u), "danger"),
    );
    tbody.appendChild(tr);
  }
  if (rows.length === 0) {
    tbody.innerHTML = '<tr class="admin-empty-row"><td colspan="6">Nenhum usuário corresponde aos filtros.</td></tr>';
  }
}

function adminMiniBtn(label, onclick, cls) {
  const b = document.createElement("button");
  b.textContent = label;
  b.className = `admin-action ${cls === "danger" ? "admin-action-danger" : ""}`;
  b.onclick = onclick;
  return b;
}

// --- Modal plumbing ---

function closeAdminModal() {
  closeDialog("admin-modal");
  document.getElementById("admin-modal-body").innerHTML = "";
}

function openCreateUserModal() {
  document.getElementById("admin-modal-title").textContent = "Criar usuário";
  const body = document.getElementById("admin-modal-body");
  body.innerHTML =
    labeled("Usuário", '<input id="mu-username" type="text" autocomplete="off" />') +
    labeled("Senha", '<input id="mu-password" type="password" autocomplete="new-password" />') +
    labeled(
      "Role",
      '<select id="mu-role"><option value="user">user</option><option value="admin">admin</option></select>',
    ) +
    `<div class="row"><button id="mu-submit">Criar</button></div>`;
  openDialog("admin-modal", { initialFocus: "#mu-username" });
  document.getElementById("mu-submit").onclick = submitCreateUser;
}

async function submitCreateUser() {
  const username = document.getElementById("mu-username").value.trim();
  const password = document.getElementById("mu-password").value;
  const role = document.getElementById("mu-role").value;
  if (username.length < 3 || password.length < 8) {
    setMsg("admin-msg", "Usuário ≥3 e senha ≥8 caracteres.", "error");
    return;
  }
  try {
    await api("/api/admin/users", {
      method: "POST",
      body: { username, password, role },
    });
    closeAdminModal();
    await loadAdminPanel();
    setMsg("admin-msg", `Usuário "${username}" criado.`, "ok");
  } catch (e) {
    setMsg("admin-msg", "Erro ao criar: " + e.message, "error");
  }
}

function openEditUserModal(u) {
  document.getElementById("admin-modal-title").textContent = `Editar ${u.username}`;
  const body = document.getElementById("admin-modal-body");
  const tokenPlaceholder = ""; // empty means "keep default"
  body.innerHTML =
    labeled(
      "Limite diário de tokens (vazio = padrão)",
      `<input id="eu-token-limit" type="number" min="0" value="${u.daily_token_limit ?? tokenPlaceholder}" />`,
    ) +
    labeled(
      "Limite diário de minutos (vazio = padrão)",
      `<input id="eu-min-limit" type="number" min="0" value="${u.daily_minutes_limit ?? tokenPlaceholder}" />`,
    ) +
    labeled(
      "Ilimitado",
      `<label><input id="eu-unlimited" type="checkbox" ${u.unlimited ? "checked" : ""} /> isento de quota</label>`,
    ) +
    labeled(
      "Role",
      `<select id="eu-role"><option value="user" ${u.role === "user" ? "selected" : ""}>user</option><option value="admin" ${u.role === "admin" ? "selected" : ""}>admin</option></select>`,
    ) +
    `<div class="row"><button id="eu-submit">Salvar</button></div>`;
  openDialog("admin-modal", { initialFocus: "#eu-token-limit" });
  document.getElementById("eu-submit").onclick = () => submitEditUser(u.id);
}

async function submitEditUser(id) {
  const tokRaw = document.getElementById("eu-token-limit").value.trim();
  const minRaw = document.getElementById("eu-min-limit").value.trim();
  const unlimited = document.getElementById("eu-unlimited").checked;
  const role = document.getElementById("eu-role").value;
  const patch = { unlimited, role };
  if (tokRaw !== "") patch.daily_token_limit = Number(tokRaw);
  if (minRaw !== "") patch.daily_minutes_limit = Number(minRaw);
  try {
    await api(`/api/admin/users/${id}`, { method: "PATCH", body: patch });
    closeAdminModal();
    await loadAdminPanel();
    setMsg("admin-msg", "Usuário atualizado.", "ok");
  } catch (e) {
    setMsg("admin-msg", "Erro ao atualizar: " + e.message, "error");
  }
}

function openResetPasswordModal(u) {
  document.getElementById("admin-modal-title").textContent = `Resetar senha — ${u.username}`;
  const body = document.getElementById("admin-modal-body");
  body.innerHTML =
    labeled(
      "Nova senha",
      '<input id="rp-password" type="password" autocomplete="new-password" />',
    ) +
    `<div class="row"><button id="rp-submit">Resetar senha</button></div>`;
  openDialog("admin-modal", { initialFocus: "#rp-password" });
  document.getElementById("rp-submit").onclick = () => submitResetPassword(u.id);
}

async function submitResetPassword(id) {
  const password = document.getElementById("rp-password").value;
  if (password.length < 8) {
    setMsg("admin-msg", "Senha ≥8 caracteres.", "error");
    return;
  }
  try {
    await api(`/api/admin/users/${id}/reset-password`, {
      method: "POST",
      body: { new_password: password },
    });
    closeAdminModal();
    setMsg("admin-msg", "Senha redefinida.", "ok");
  } catch (e) {
    setMsg("admin-msg", "Erro: " + e.message, "error");
  }
}

async function adminToggleActive(u) {
  const verb = u.active ? "desativar" : "reativar";
  if (!await confirmAction(`Confirmar ${verb} "${u.username}"?`, {
    title: `${u.active ? "Desativar" : "Reativar"} usuário`,
    confirmLabel: u.active ? "Desativar" : "Reativar",
    danger: u.active,
  })) return;
  try {
    await api(`/api/admin/users/${u.id}`, {
      method: "PATCH",
      body: { active: !u.active },
    });
    await loadAdminPanel();
    setMsg("admin-msg", `"${u.username}" ${u.active ? "desativado" : "reativado"}.`, "ok");
  } catch (e) {
    setMsg("admin-msg", "Erro: " + e.message, "error");
  }
}

async function adminDeleteUser(u) {
  if (!await confirmAction(
    `Excluir “${u.username}” (id ${u.id})? Saves e histórico também serão apagados. Esta ação é irreversível.`,
    { title: "Excluir usuário", confirmLabel: "Excluir definitivamente", danger: true },
  )) return;
  try {
    await api(`/api/admin/users/${u.id}`, { method: "DELETE" });
    await loadAdminPanel();
    setMsg("admin-msg", `"${u.username}" excluído.`, "ok");
  } catch (e) {
    setMsg("admin-msg", "Erro: " + e.message, "error");
  }
}

function closeAdminDetail() {
  closeDialog("admin-detail");
}

async function loadUserDetail(id, username) {
  document.getElementById("admin-detail-title").textContent = `${username} — atividade`;
  document.getElementById("admin-activity-list").innerHTML =
    '<li class="meta">Carregando…</li>';
  document.getElementById("admin-usage-list").innerHTML =
    '<li class="meta">Carregando…</li>';
  openDialog("admin-detail", { initialFocus: "#admin-detail-close" });
  try {
    const [activity, usage] = await Promise.all([
      api(`/api/admin/users/${id}/activity?limit=50`),
      api(`/api/admin/users/${id}/usage?days=30`),
    ]);
    renderUserActivity(activity.activity || []);
    renderUserUsage(usage.series || []);
  } catch (e) {
    setMsg("admin-msg", "Erro ao carregar detalhes: " + e.message, "error");
  }
}

const ACTIVITY_LABELS = {
  login: "Login",
  logout: "Logout",
  signup: "Cadastro",
  session_start: "Início de sessão",
  limit_blocked: "Bloqueado por limite",
  disabled: "Desativado",
  reenabled: "Reativado",
  password_reset: "Senha redefinida",
  user_created: "Usuário criado",
  user_deleted: "Usuário excluído",
  limit_override: "Limite alterado",
};

function renderUserActivity(rows) {
  const ul = document.getElementById("admin-activity-list");
  ul.innerHTML = "";
  if (rows.length === 0) {
    ul.innerHTML = '<li class="meta">Sem registros.</li>';
    return;
  }
  for (const r of rows) {
    const li = document.createElement("li");
    const label = ACTIVITY_LABELS[r.event_type] || r.event_type;
    li.innerHTML = `<span class="meta">${r.created_at}</span> · ${label}` +
      (r.meta ? ` <span class="meta">${JSON.stringify(r.meta)}</span>` : "");
    ul.appendChild(li);
  }
}

function renderUserUsage(series) {
  const ul = document.getElementById("admin-usage-list");
  ul.innerHTML = "";
  const nonzero = series.filter((d) => d.tokens > 0);
  if (nonzero.length === 0) {
    ul.innerHTML = '<li class="meta">Sem uso registrado.</li>';
    return;
  }
  for (const d of [...nonzero].reverse()) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="meta">${d.date}</span> · ${d.tokens.toLocaleString("pt-BR")} tok · $${Number(d.cost || 0).toFixed(4)}`;
    ul.appendChild(li);
  }
}

function labeled(text, control) {
  return `<label><span class="lbl">${text}</span>${control}</label>`;
}


// ============================================================================
// Wizard (Phase 26c)
// ============================================================================

const WIZARD_STEPS = [
  "name", "race", "class", "subclass",
  "background", "alignment", "level", "stats",
  "skills", "spells", "companions", "confirm",
];

const WIZARD_STEP_META = [
  { label: "Nomes", short: "Nomes" },
  { label: "Raça", short: "Raça" },
  { label: "Classe", short: "Classe" },
  { label: "Subclasse", short: "Subclasse" },
  { label: "Background", short: "Passado" },
  { label: "Alinhamento", short: "Alinhamento" },
  { label: "Nível", short: "Nível" },
  { label: "Atributos", short: "Atributos" },
  { label: "Perícias", short: "Perícias" },
  { label: "Magias", short: "Magias" },
  { label: "Companheiros", short: "Grupo" },
  { label: "Revisão", short: "Revisão" },
];

function emptySpellSelection() {
  return {
    cantrips: [],
    spells_known: [],
    spells_prepared: [],
    spellbook: [],
  };
}

let wizardState = {
  step: 0,             // index into WIZARD_STEPS
  furthestStep: 0,      // enables revisiting completed steps without skipping ahead
  options: null,        // loaded catalog from /api/character-options
  campaign_name: "",
  // Per-campaign DM narration length. Default "longo" preserves the
  // original verbose behavior when the player doesn't change it.
  narration_length: "longo",
  // Cenário inicial opcional descrito pelo jogador. Vazio = LLM decide.
  initial_scenario: "",
  name: "",
  race: null,
  subrace: null,
  char_class: null,
  subclass: null,
  background: null,
  alignment: null,
  level: 1,
  stats_method: "standard_array",
  skills: [],
  spell_selection: emptySpellSelection(),
  companions: [],
  // Phase 27: companions are rolled lazily from /api/companions/roll when
  // the player reaches the companions step (synergy-biased against wizardState.char_class).
  companionCandidates: null,
};

async function openWizard() {
  show("wizard-screen");
  setWizardLoadState("loading");
  setMsg("lobby-msg", "", "");
  try {
    wizardState.options = await api("/api/character-options");
    wizardState.step = 0;
    wizardState.furthestStep = 0;
    // Companions start empty; renderWizardCompanions rolls 4 candidates
    // against the player's class the first time step 10 is reached.
    wizardState.companions = [];
    wizardState.companionCandidates = null;
    wizardState.spell_selection = emptySpellSelection();
    setWizardLoadState("ready");
    renderWizardStep();
  } catch (e) {
    setWizardLoadState("error", e.message);
  }
}

function setWizardLoadState(state, detail = "") {
  const loading = document.getElementById("wizard-loading");
  const error = document.getElementById("wizard-load-error");
  const workspace = document.getElementById("wizard-workspace");
  loading.hidden = state !== "loading";
  error.hidden = state !== "error";
  workspace.hidden = state !== "ready";
  document.getElementById("wizard-screen").setAttribute(
    "aria-busy",
    String(state === "loading"),
  );
  if (state === "error") {
    document.getElementById("wizard-load-error-text").textContent =
      detail ? `Detalhes: ${detail}` : "Confira sua conexão e tente novamente.";
  }
}

function renderWizardStep() {
  // Hide all, show current.
  for (let i = 1; i <= WIZARD_STEPS.length; i++) {
    const el = document.getElementById(`wizard-step-${i}`);
    if (el) el.classList.toggle("active", i === wizardState.step + 1);
  }
  // Legible progress: current, completed and pending steps.
  const prog = document.getElementById("wizard-progress");
  prog.innerHTML = "";
  for (let i = 0; i < WIZARD_STEPS.length; i++) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    const isCurrent = i === wizardState.step;
    const isComplete = i <= wizardState.furthestStep && i !== wizardState.step;
    button.type = "button";
    button.className = "wizard-progress-item" +
      (isCurrent ? " is-current" : "") +
      (isComplete ? " is-complete" : "");
    button.disabled = i > wizardState.furthestStep;
    button.setAttribute("aria-label", `${i + 1}. ${WIZARD_STEP_META[i].label}`);
    if (isCurrent) button.setAttribute("aria-current", "step");
    const number = document.createElement("span");
    number.className = "wizard-progress-number";
    number.textContent = isComplete ? "✓" : String(i + 1);
    number.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.className = "wizard-progress-label";
    label.textContent = WIZARD_STEP_META[i].label;
    button.appendChild(number);
    button.appendChild(label);
    button.onclick = () => wizardGoToStep(i);
    item.appendChild(button);
    prog.appendChild(item);
  }
  // Counter.
  document.getElementById("wz-step-counter").textContent =
    `Passo ${wizardState.step + 1} / ${WIZARD_STEPS.length}`;
  // Buttons.
  document.getElementById("wz-prev").style.display = wizardState.step === 0 ? "none" : "";
  const isLast = wizardState.step === WIZARD_STEPS.length - 1;
  document.getElementById("wz-next").style.display = isLast ? "none" : "";
  document.getElementById("wz-finish").style.display = isLast ? "" : "none";
  // Render the active step's content.
  const stepName = WIZARD_STEPS[wizardState.step];
  switch (stepName) {
    case "name": renderWizardName(); break;
    case "race": renderWizardRace(); break;
    case "class": renderWizardClass(); break;
    case "subclass": renderWizardSubclass(); break;
    case "background": renderWizardBackground(); break;
    case "alignment": renderWizardAlignment(); break;
    case "level": renderWizardLevel(); break;
    case "stats": renderWizardStats(); break;
    case "skills": renderWizardSkills(); break;
    case "spells": renderWizardSpells(); break;
    case "companions": renderWizardCompanions(); break;
    case "confirm": renderWizardConfirm(); break;
  }
  renderWizardPersistentSummary();
  setMsg("wizard-msg", "", "");
  requestAnimationFrame(() => {
    const activeTitle = document.querySelector(".wizard-step.active h2");
    activeTitle?.setAttribute("tabindex", "-1");
    activeTitle?.focus({ preventScroll: true });
  });
}

function wizardGoToStep(step) {
  if (step < 0 || step >= WIZARD_STEPS.length || step > wizardState.furthestStep) return;
  wizardState.step = step;
  renderWizardStep();
  document.getElementById("wizard-screen").scrollIntoView({ block: "start" });
}

function createWizardChoice({ name, description = "", selected = false, recommended = false,
  disabled = false, onSelect }) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "choice" + (selected ? " selected" : "") +
    (disabled ? " is-unavailable" : "");
  button.disabled = disabled;
  button.setAttribute("aria-pressed", String(selected));
  const title = document.createElement("span");
  title.className = "name";
  title.textContent = name;
  button.appendChild(title);
  if (description) {
    const desc = document.createElement("span");
    desc.className = "desc";
    desc.textContent = description;
    button.appendChild(desc);
  }
  if (recommended) {
    const badge = document.createElement("span");
    badge.className = "choice-badge";
    badge.textContent = "Recomendado";
    button.appendChild(badge);
  }
  button.onclick = onSelect;
  return button;
}

function wizardCompanionNames() {
  const catalog = wizardState.options?.companions || [];
  return wizardState.companions.map(
    (key) => catalog.find((companion) => companion.key === key)?.name || key,
  );
}

function wizardPersistentSummaryRows() {
  const origin = [wizardState.race, wizardState.subrace].filter(Boolean).join(" · ");
  const vocation = [wizardState.char_class, wizardState.subclass].filter(Boolean).join(" · ");
  return [
    ["Personagem", wizardState.name || "A definir"],
    ["Origem", origin || "A definir"],
    ["Vocação", vocation || "A definir"],
    ["Background", wizardState.background || "A definir"],
    ["Nível", wizardState.level ? `Nível ${wizardState.level}` : "A definir"],
    ["Grupo", wizardCompanionNames().join(", ") || "A definir"],
  ];
}

function renderWizardMiniSheet(root) {
  if (!root) return;
  const list = document.createElement("dl");
  list.className = "wizard-mini-sheet";
  for (const [label, value] of wizardPersistentSummaryRows()) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value;
    description.classList.toggle("is-empty", value === "A definir");
    row.appendChild(term);
    row.appendChild(description);
    list.appendChild(row);
  }
  root.replaceChildren(list);
}

function renderWizardPersistentSummary() {
  renderWizardMiniSheet(document.getElementById("wizard-summary-sidebar"));
  renderWizardMiniSheet(document.getElementById("wizard-summary-mobile"));
  const mobileLine = document.getElementById("wizard-mobile-summary-line");
  if (mobileLine) {
    mobileLine.textContent = [wizardState.name, wizardState.race, wizardState.char_class]
      .filter(Boolean)
      .join(" · ") || "Novo personagem";
  }
}

// --- Step renderers ---

function renderWizardName() {
  document.getElementById("wz-campaign-name").value = wizardState.campaign_name;
  document.getElementById("wz-char-name").value = wizardState.name;
  document.getElementById("wz-campaign-name").oninput = (e) => {
    wizardState.campaign_name = e.target.value;
    renderWizardPersistentSummary();
  };
  document.getElementById("wz-char-name").oninput = (e) => {
    wizardState.name = e.target.value;
    renderWizardPersistentSummary();
  };
  // Narration length: prefer the catalog when available, fall back to the
  // hard-coded options in index.html (which still ship as a defensive default).
  const sel = document.getElementById("wz-narration-length");
  if (sel) {
    const opts = (wizardState.options && wizardState.options.narration_lengths)
      || [
        { id: "curto", label: "Curto (1-2 frases, tensão ainda mais seca)" },
        { id: "medio", label: "Médio (3-5 frases, com detalhe sensorial moderado)" },
        { id: "longo", label: "Longo (1-2 parágrafos, prosa rica — modo atual)" },
      ];
    sel.innerHTML = opts
      .map((o) => `<option value="${o.id}" ${wizardState.narration_length === o.id ? "selected" : ""}>${o.label}</option>`)
      .join("");
    sel.onchange = (e) => {
      wizardState.narration_length = e.target.value;
    };
  }
  // Cenário inicial (opcional). Vazio = mestre decide livremente.
  const scenario = document.getElementById("wz-initial-scenario");
  if (scenario) {
    scenario.value = wizardState.initial_scenario || "";
    scenario.oninput = (e) => {
      wizardState.initial_scenario = e.target.value;
    };
  }
}

// Phase 35: ask the backend LLM to invent a campaign/character name and
// fill the matching input. `kind` is "campaign" | "character". Each ✨
// button next to a name field calls this. Best-effort UX: the button shows
// a spinner while waiting, failures land in #wizard-msg (never throw).
async function suggestWizardName(kind) {
  const btnId = kind === "campaign" ? "wz-campaign-name-ai" : "wz-char-name-ai";
  const btn = document.getElementById(btnId);
  const inputId = kind === "campaign" ? "wz-campaign-name" : "wz-char-name";
  const input = document.getElementById(inputId);
  if (!btn || !input) return;
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = "⏳";
  setMsg("wizard-msg", "Gerando nome com IA...", "");
  try {
    const res = await api("/api/suggest-names", {
      method: "POST",
      body: { kind },
    });
    const value = kind === "campaign" ? res.campaign_name : res.character_name;
    if (!value) {
      setMsg("wizard-msg", "A IA retornou um nome vazio. Tente novamente.", "error");
      return;
    }
    input.value = value;
    if (kind === "campaign") wizardState.campaign_name = value;
    else wizardState.name = value;
    renderWizardPersistentSummary();
    setMsg("wizard-msg", "", "");
  } catch (e) {
    if (e && e.status === 429) {
      setMsg("wizard-msg", LIMIT_REACHED_MSG, "error");
    } else {
      setMsg("wizard-msg", "Não foi possível gerar o nome: " + e.message, "error");
    }
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
  }
}

function renderWizardRace() {
  const root = document.getElementById("wz-races");
  root.innerHTML = "";
  for (const r of wizardState.options.races) {
    root.appendChild(createWizardChoice({
      name: r.name,
      description: `${r.size} · ${r.speed} ft${r.subraces.length ? ` · ${r.subraces.length} sub-raças` : ""}`,
      selected: wizardState.race === r.name,
      onSelect: () => {
        wizardState.race = r.name;
        wizardState.subrace = null;
        renderWizardStep();
      },
    }));
  }
  // Subrace dropdown (only if selected race has subraces and is selected).
  const wrap = document.getElementById("wz-subrace-wrap");
  const sel = document.getElementById("wz-subrace");
  if (wizardState.race) {
    const race = wizardState.options.races.find((r) => r.name === wizardState.race);
    if (race && race.subraces.length > 0) {
      wrap.style.display = "";
      sel.innerHTML = `<option value="">(nenhuma)</option>` +
        race.subraces.map((s) => `<option value="${s}" ${s === wizardState.subrace ? "selected" : ""}>${s}</option>`).join("");
      sel.onchange = (e) => {
        wizardState.subrace = e.target.value || null;
        renderWizardPersistentSummary();
      };
    } else {
      wrap.style.display = "none";
    }
  } else {
    wrap.style.display = "none";
  }
}

function renderWizardClass() {
  const root = document.getElementById("wz-classes");
  root.innerHTML = "";
  for (const c of wizardState.options.classes) {
    root.appendChild(createWizardChoice({
      name: c.name,
      description: `${c.hit_dice} · ${c.num_skill_choices} perícias${c.is_spellcaster ? " · conjurador" : ""}`,
      selected: wizardState.char_class === c.name,
      onSelect: () => {
        wizardState.char_class = c.name;
        wizardState.subclass = null;
        wizardState.skills = [];
        wizardState.spell_selection = emptySpellSelection();
        wizardState.companions = [];
        wizardState.companionCandidates = null;
        renderWizardStep();
      },
    }));
  }
}

function renderWizardSubclass() {
  const root = document.getElementById("wz-subclasses");
  root.innerHTML = "";
  if (!wizardState.char_class) {
    root.innerHTML = '<div class="wizard-empty is-unavailable">Escolha uma classe primeiro.</div>';
    return;
  }
  const cls = wizardState.options.classes.find((c) => c.name === wizardState.char_class);
  if (!cls || !cls.subclasses.length) {
    root.innerHTML = '<div class="wizard-empty is-unavailable">Esta classe não tem subclasses disponíveis nesta criação. Você pode continuar.</div>';
    return;
  }
  for (const s of cls.subclasses) {
    root.appendChild(createWizardChoice({
      name: s,
      selected: wizardState.subclass === s,
      onSelect: () => {
        wizardState.subclass = wizardState.subclass === s ? null : s;
        wizardState.companions = [];
        wizardState.companionCandidates = null;
        renderWizardStep();
      },
    }));
  }
}

function renderWizardBackground() {
  const root = document.getElementById("wz-backgrounds");
  root.innerHTML = "";
  for (const b of wizardState.options.backgrounds) {
    root.appendChild(createWizardChoice({
      name: b.name,
      description: b.feature || "",
      selected: wizardState.background === b.name,
      onSelect: () => {
        wizardState.background = b.name;
        renderWizardStep();
      },
    }));
  }
}

function renderWizardAlignment() {
  const root = document.getElementById("wz-alignments");
  root.innerHTML = "";
  for (const a of wizardState.options.alignments) {
    root.appendChild(createWizardChoice({
      name: a,
      selected: wizardState.alignment === a,
      onSelect: () => {
        wizardState.alignment = a;
        renderWizardStep();
      },
    }));
  }
}

function renderWizardLevel() {
  const root = document.getElementById("wz-levels");
  root.innerHTML = "";
  for (const lv of wizardState.options.levels) {
    root.appendChild(createWizardChoice({
      name: `Nível ${lv}`,
      description: lv === 1 ? "Comece com as bases da classe." : "Mais recursos desde o início.",
      selected: wizardState.level === lv,
      recommended: lv === 1,
      onSelect: () => {
        wizardState.level = lv;
        wizardState.spell_selection = emptySpellSelection();
        renderWizardStep();
      },
    }));
  }
}

function renderWizardStats() {
  const root = document.getElementById("wz-stats-methods");
  root.innerHTML = "";
  for (const m of wizardState.options.stats_methods) {
    root.appendChild(createWizardChoice({
      name: m.label,
      selected: wizardState.stats_method === m.id,
      recommended: m.id === "standard_array",
      onSelect: () => {
        wizardState.stats_method = m.id;
        renderWizardStep();
      },
    }));
  }
  const info = document.getElementById("wz-stats-info");
  if (wizardState.stats_method === "roll") {
    info.textContent = "Os dados serão rolados no servidor (4d6, drop lowest, 6 rolagens).";
  } else if (wizardState.stats_method === "point_buy") {
    info.textContent = "Compra de pontos (PHB p.13): 27 pontos, scores 8–15.";
  } else {
    info.textContent = "Standard Array: 15, 14, 13, 12, 10, 8 distribuídos automaticamente.";
  }
}

function renderWizardSkills() {
  const root = document.getElementById("wz-skills");
  root.innerHTML = "";
  if (!wizardState.char_class) {
    document.getElementById("wz-skills-info").textContent = "Escolha uma classe primeiro.";
    return;
  }
  const cls = wizardState.options.classes.find((c) => c.name === wizardState.char_class);
  const opts = (cls && cls.skill_options) || [];
  const num = (cls && cls.num_skill_choices) || 0;
  document.getElementById("wz-skills-info").textContent =
    num > 0
      ? `Escolha até ${num} perícia(s) da lista abaixo. ${wizardState.skills.length}/${num} selecionadas.`
      : "Esta classe não concede escolhas de perícia.";
  for (const s of opts) {
    const lbl = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = wizardState.skills.includes(s);
    cb.disabled = num > 0 && !cb.checked && wizardState.skills.length >= num;
    cb.onchange = () => {
      if (cb.checked) {
        if (!wizardState.skills.includes(s)) wizardState.skills.push(s);
      } else {
        wizardState.skills = wizardState.skills.filter((x) => x !== s);
      }
      renderWizardSkills();
      renderWizardPersistentSummary();
    };
    const span = document.createElement("span");
    span.textContent = s;
    lbl.appendChild(cb);
    lbl.appendChild(span);
    root.appendChild(lbl);
  }
}

function wizardClassOption() {
  if (!wizardState.char_class || !wizardState.options) return null;
  return wizardState.options.classes.find((c) => c.name === wizardState.char_class) || null;
}

function wizardSpellLimit() {
  const cls = wizardClassOption();
  if (!cls || !cls.spellcasting) return null;
  return cls.spellcasting.limits[String(wizardState.level)] || null;
}

function wizardPreparedMax() {
  const cls = wizardClassOption();
  if (!cls || !cls.spellcasting) return 0;
  const abilityScores = {
    strength: 15,
    dexterity: 14,
    constitution: 13,
    intelligence: 12,
    wisdom: 10,
    charisma: 8,
  };
  const score = abilityScores[cls.spellcasting.ability] || 10;
  const mod = Math.floor((score - 10) / 2);
  if (wizardState.char_class === "Paladin") {
    return Math.max(1, mod + Math.floor(wizardState.level / 2));
  }
  return Math.max(1, mod + wizardState.level);
}

function spellMeta(spell) {
  const tags = [
    spell.level === 0 ? "truque" : `${spell.level} nivel`,
    spell.school,
  ];
  if (spell.ritual) tags.push("ritual");
  if (spell.concentration) tags.push("concentracao");
  return tags.join(" - ");
}

function renderSpellCheckboxList(root, title, spells, selected, max, field) {
  const section = document.createElement("section");
  section.className = "spell-section";
  const h = document.createElement("h3");
  h.textContent = max > 0 ? `${title} (${selected.length}/${max})` : title;
  section.appendChild(h);

  if (!spells.length || max === 0) {
    const empty = document.createElement("div");
    empty.className = "wizard-empty is-unavailable";
    empty.textContent = "Nenhuma escolha disponivel neste nivel.";
    section.appendChild(empty);
    root.appendChild(section);
    return;
  }

  const list = document.createElement("div");
  list.className = "spell-list";
  for (const spell of spells) {
    const lbl = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = selected.includes(spell.name);
    cb.disabled = !cb.checked && selected.length >= max;
    cb.onchange = () => {
      if (cb.checked) {
        if (!wizardState.spell_selection[field].includes(spell.name)) {
          wizardState.spell_selection[field].push(spell.name);
        }
      } else {
        wizardState.spell_selection[field] =
          wizardState.spell_selection[field].filter((x) => x !== spell.name);
        if (field === "spellbook") {
          wizardState.spell_selection.spells_prepared =
            wizardState.spell_selection.spells_prepared.filter((x) =>
              wizardState.spell_selection.spellbook.includes(x)
            );
        }
      }
      renderWizardSpells();
      renderWizardPersistentSummary();
    };

    const text = document.createElement("span");
    const name = document.createElement("span");
    name.className = "spell-name";
    name.textContent = spell.name;
    const meta = document.createElement("span");
    meta.className = "spell-meta";
    meta.textContent = spellMeta(spell);
    text.appendChild(name);
    text.appendChild(meta);
    lbl.appendChild(cb);
    lbl.appendChild(text);
    list.appendChild(lbl);
  }
  section.appendChild(list);
  root.appendChild(section);
}

function renderWizardSpells() {
  const root = document.getElementById("wz-spells");
  const info = document.getElementById("wz-spells-info");
  root.innerHTML = "";
  const cls = wizardClassOption();
  const limit = wizardSpellLimit();
  if (!cls || !cls.spellcasting || !limit) {
    info.textContent = "Esta classe nao usa magias na criacao.";
    return;
  }

  const spellcasting = cls.spellcasting;
  const spells = spellcasting.spells || [];
  const slotLevels = new Set(limit.slot_levels || []);
  const cantrips = spells.filter((s) => s.level === 0);
  const leveled = spells.filter((s) => s.level > 0 && slotLevels.has(s.level));
  const selection = wizardState.spell_selection;
  const type = spellcasting.caster_type;

  if (!limit.cantrips_known && !slotLevels.size) {
    info.textContent = `${wizardState.char_class} ainda nao recebe magias no nivel ${wizardState.level}.`;
    return;
  }

  const ability = spellcasting.ability;
  info.textContent =
    `Habilidade de conjuracao: ${ability}. Escolha as magias iniciais do personagem.`;

  renderSpellCheckboxList(
    root,
    "Truques",
    cantrips,
    selection.cantrips,
    limit.cantrips_known || 0,
    "cantrips",
  );

  if (type === "known") {
    renderSpellCheckboxList(
      root,
      "Magias conhecidas",
      leveled,
      selection.spells_known,
      limit.spells_known || 0,
      "spells_known",
    );
  } else if (type === "prepared") {
    renderSpellCheckboxList(
      root,
      "Magias preparadas",
      leveled,
      selection.spells_prepared,
      slotLevels.size ? wizardPreparedMax() : 0,
      "spells_prepared",
    );
  } else if (type === "wizard") {
    renderSpellCheckboxList(
      root,
      "Grimorio",
      leveled,
      selection.spellbook,
      limit.spellbook_size || 0,
      "spellbook",
    );
    const preparedSource = leveled.filter((s) => selection.spellbook.includes(s.name));
    renderSpellCheckboxList(
      root,
      "Magias preparadas",
      preparedSource,
      selection.spells_prepared,
      wizardPreparedMax(),
      "spells_prepared",
    );
  }
}

function renderWizardCompanions() {
  const root = document.getElementById("wz-companions");
  root.innerHTML = "";
  if (!wizardState.char_class) {
    setMsg("wizard-msg", "Escolha a classe antes de rolar os companheiros.", "error");
    return;
  }
  // Lazy-fetch the 4 synergy-biased candidates the first time we reach
  // this step. Subsequent re-renders (e.g. toggling checkboxes) reuse
  // the cached candidates so we don't re-roll on every navigation.
  if (!wizardState.companionCandidates) {
    root.setAttribute("aria-busy", "true");
    root.innerHTML = [1, 2, 3, 4].map(() =>
      '<div class="wizard-companion-skeleton" aria-hidden="true"></div>'
    ).join("");
    api("/api/companions/roll", {
      method: "POST",
      body: {
        class: wizardState.char_class,
        subclass: wizardState.subclass || null,
      },
    })
      .then((res) => {
        wizardState.companionCandidates = res.candidates;
        // Default: all 4 candidates selected.
        if (wizardState.companions.length === 0) {
          wizardState.companions = res.candidates.map((c) => c.key);
        }
        root.setAttribute("aria-busy", "false");
        renderWizardCompanions();
        renderWizardPersistentSummary();
      })
      .catch((e) => {
        root.setAttribute("aria-busy", "false");
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "button button-secondary";
        retry.textContent = "Tentar novamente";
        retry.onclick = renderWizardCompanions;
        root.replaceChildren(retry);
        setMsg("wizard-msg", "Erro ao rolar companheiros: " + e.message, "error");
      });
    setMsg("wizard-msg", "Rolando companheiros...", "");
    return;
  }
  for (const c of wizardState.companionCandidates) {
    const lbl = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = wizardState.companions.includes(c.key);
    cb.onchange = () => {
      if (cb.checked) {
        if (!wizardState.companions.includes(c.key)) wizardState.companions.push(c.key);
      } else {
        wizardState.companions = wizardState.companions.filter((x) => x !== c.key);
      }
      renderWizardPersistentSummary();
    };
    const span = document.createElement("span");
    span.innerHTML = `<b>${c.name}</b> — <i>${c.race} ${c.class_ || ""}</i>: ${c.description || ""}`;
    lbl.appendChild(cb);
    lbl.appendChild(span);
    root.appendChild(lbl);
  }
}

function renderWizardConfirm() {
  const root = document.getElementById("wz-summary");
  const sections = [
    {
      title: "Aventura",
      step: 0,
      rows: [
        ["Campanha", wizardState.campaign_name || "A definir"],
        ["Personagem", wizardState.name || "A definir"],
        ["Narração", wizardState.narration_length],
        ["Cenário inicial", wizardState.initial_scenario || "O Mestre decide"],
      ],
    },
    {
      title: "Identidade",
      step: 1,
      rows: [
        ["Raça", [wizardState.race, wizardState.subrace].filter(Boolean).join(" · ")],
        ["Classe", [wizardState.char_class, wizardState.subclass].filter(Boolean).join(" · ")],
        ["Background", wizardState.background],
        ["Alinhamento", wizardState.alignment],
        ["Nível", `Nível ${wizardState.level}`],
      ],
    },
    {
      title: "Habilidades",
      step: 7,
      rows: [
        ["Atributos", wizardState.stats_method],
        ["Perícias", wizardState.skills.join(", ") || "Nenhuma"],
        ["Truques", wizardState.spell_selection.cantrips.join(", ") || "Nenhum"],
        ["Magias", wizardSpellSummary()],
      ],
    },
    {
      title: "Grupo",
      step: 10,
      rows: [["Companheiros", wizardCompanionNames().join(", ") || "Aventura solo"]],
    },
  ];
  const review = document.createElement("div");
  review.className = "wizard-review";
  for (const sectionData of sections) {
    const section = document.createElement("section");
    section.className = "wizard-review-section";
    const heading = document.createElement("div");
    heading.className = "wizard-review-heading";
    const title = document.createElement("h3");
    title.textContent = sectionData.title;
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "wizard-review-edit";
    edit.textContent = "Editar";
    edit.setAttribute("aria-label", `Editar ${sectionData.title.toLowerCase()}`);
    edit.onclick = () => wizardGoToStep(sectionData.step);
    heading.appendChild(title);
    heading.appendChild(edit);
    const list = document.createElement("dl");
    list.className = "wizard-review-list";
    for (const [label, rawValue] of sectionData.rows) {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const value = document.createElement("dd");
      term.textContent = label;
      value.textContent = rawValue || "A definir";
      row.appendChild(term);
      row.appendChild(value);
      list.appendChild(row);
    }
    section.appendChild(heading);
    section.appendChild(list);
    review.appendChild(section);
  }
  root.replaceChildren(review);
}

function wizardSpellSummary() {
  const s = wizardState.spell_selection;
  const parts = [];
  if (s.spells_known.length) parts.push(`conhecidas: ${s.spells_known.join(", ")}`);
  if (s.spellbook.length) parts.push(`grimorio: ${s.spellbook.join(", ")}`);
  if (s.spells_prepared.length) parts.push(`preparadas: ${s.spells_prepared.join(", ")}`);
  return parts.join(" | ") || "(nenhuma)";
}

function wizardValidateSpells() {
  const cls = wizardClassOption();
  const limit = wizardSpellLimit();
  if (!cls || !cls.spellcasting || !limit) return null;
  const selection = wizardState.spell_selection;
  const type = cls.spellcasting.caster_type;
  const slots = limit.slot_levels || [];

  if ((limit.cantrips_known || 0) > 0 &&
      selection.cantrips.length !== limit.cantrips_known) {
    return `Escolha ${limit.cantrips_known} truque(s) para ${wizardState.char_class}.`;
  }
  if (type === "known" && (limit.spells_known || 0) > 0 &&
      selection.spells_known.length !== limit.spells_known) {
    return `Escolha ${limit.spells_known} magia(s) conhecida(s) para ${wizardState.char_class}.`;
  }
  if (type === "prepared" && slots.length > 0 && selection.spells_prepared.length < 1) {
    return `Escolha pelo menos 1 magia preparada para ${wizardState.char_class}.`;
  }
  if (type === "wizard") {
    if ((limit.spellbook_size || 0) > 0 &&
        selection.spellbook.length !== limit.spellbook_size) {
      return `Escolha ${limit.spellbook_size} magia(s) para o grimorio.`;
    }
    if (selection.spells_prepared.length < 1) {
      return "Escolha pelo menos 1 magia preparada.";
    }
  }
  return null;
}

function hasWizardSpellSelection() {
  const s = wizardState.spell_selection;
  return Boolean(
    s.cantrips.length ||
    s.spells_known.length ||
    s.spells_prepared.length ||
    s.spellbook.length
  );
}

// --- Navigation ---

function wizardPrev() {
  if (wizardState.step > 0) {
    wizardGoToStep(wizardState.step - 1);
  }
}

function wizardNext() {
  const err = wizardValidateStep(wizardState.step);
  if (err) {
    document.getElementById(`wizard-step-${wizardState.step + 1}`)?.classList.add("has-error");
    setMsg("wizard-msg", err, "error");
    return;
  }
  if (wizardState.step < WIZARD_STEPS.length - 1) {
    wizardState.step++;
    wizardState.furthestStep = Math.max(wizardState.furthestStep, wizardState.step);
    renderWizardStep();
    document.getElementById("wizard-screen").scrollIntoView({ block: "start" });
  }
}

function wizardValidateStep(step) {
  switch (WIZARD_STEPS[step]) {
    case "name":
      if (!wizardState.campaign_name.trim()) return "Informe o nome da campanha.";
      if (!wizardState.name.trim()) return "Informe o nome do personagem.";
      return null;
    case "race":
      if (!wizardState.race) return "Escolha uma raça.";
      return null;
    case "class":
      if (!wizardState.char_class) return "Escolha uma classe.";
      return null;
    case "background":
      if (!wizardState.background) return "Escolha um background.";
      return null;
    case "alignment":
      if (!wizardState.alignment) return "Escolha um alinhamento.";
      return null;
    case "level":
      if (!wizardState.level) return "Escolha um nível.";
      return null;
    case "stats":
      if (!wizardState.stats_method) return "Escolha um método de atributos.";
      return null;
    case "skills": {
      const cls = wizardState.options.classes.find((c) => c.name === wizardState.char_class);
      const num = (cls && cls.num_skill_choices) || 0;
      if (wizardState.skills.length > num) {
        return `Máximo de ${num} perícias para ${wizardState.char_class}.`;
      }
      return null;
    }
    case "spells":
      return wizardValidateSpells();
    case "companions":
      return null;  // 0 is OK
    default:
      return null;
  }
}

async function wizardFinish() {
  if (wizardValidateStep(wizardState.step)) {
    setMsg("wizard-msg", "Verifique os campos.", "error");
    return;
  }
  const btn = document.getElementById("wz-finish");
  btn.disabled = true;
  btn.classList.add("button-loading");
  setMsg("wizard-msg", "Criando personagem...", "");
  try {
    const payload = {
      campaign_name: wizardState.campaign_name,
      narration_length: wizardState.narration_length,
      // Cenário inicial opcional: string vazia vira null no backend,
      // que por sua vez trata como "mestre decide livremente".
      initial_scenario: wizardState.initial_scenario || null,
      player_character: {
        name: wizardState.name,
        race: wizardState.race,
        subrace: wizardState.subrace,
        class: wizardState.char_class,
        subclass: wizardState.subclass,
        background: wizardState.background,
        alignment: wizardState.alignment,
        level: wizardState.level,
        stats_method: wizardState.stats_method,
        skills: wizardState.skills,
      },
      companions: wizardState.companions,
    };
    if (hasWizardSpellSelection()) {
      payload.player_character.spell_selection = wizardState.spell_selection;
    }
    const res = await api("/api/sessions/with-character", {
      method: "POST",
      body: payload,
    });
    currentSessionId = res.session_id;
    currentSlug = res.slug;
    currentGameState = res.state || null;
    // Auto-save.
    await api("/api/saves", {
      method: "POST",
      body: { slug: res.slug, state: res.state },
    });
    enterGame();
    appendLog("Sistema",
      `Campanha "${res.slug}" criada com personagem ${wizardState.name}!`,
      "system");
    // Kick off the opening narration automatically (no player input needed).
    playOpening();
  } catch (e) {
    setMsg("wizard-msg", "Erro: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.classList.remove("button-loading");
  }
}
