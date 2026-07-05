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
}

// --- Screen helpers ---
function show(id) {
  for (const el of document.querySelectorAll(".screen")) {
    el.style.display = "none";
  }
  const target = document.getElementById(id);
  if (target) target.style.display = "";
}

function setMsg(id, text, kind) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text || "";
  el.className = "msg" + (kind ? " " + kind : "");
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
  w.textContent = who;
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

// --- Phase 28: input-blocking + busy feedback ---
let busy = false;

function lockUi() {
  if (busy) return;
  busy = true;
  document.getElementById("cmd").disabled = true;
  document.getElementById("send-btn").disabled = true;
  const rollBtn = document.getElementById("roll-btn");
  if (rollBtn) rollBtn.disabled = true;
  const rollCheck = document.getElementById("roll-check");
  if (rollCheck) rollCheck.disabled = true;
}

function unlockUi() {
  busy = false;
  // In read-only (admin view) mode the inputs stay disabled.
  if (readOnlyMode) return;
  document.getElementById("cmd").disabled = false;
  document.getElementById("send-btn").disabled = false;
  const rollBtn = document.getElementById("roll-btn");
  if (rollBtn) rollBtn.disabled = false;
  const rollCheck = document.getElementById("roll-check");
  if (rollCheck) rollCheck.disabled = false;
}

function showTyping() {
  const out = output();
  if (!out) return;
  const entry = document.createElement("div");
  entry.className = "entry typing-indicator";
  entry.id = "typing-indicator";
  const w = document.createElement("span");
  w.className = "who";
  w.textContent = "Sistema";
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
async function doSignup() {
  const username = document.getElementById("auth-username").value.trim();
  const password = document.getElementById("auth-password").value;
  const inviteCode = document.getElementById("auth-invite").value.trim();
  if (!username || !password) {
    setMsg("auth-msg", "Preencha usuário e senha.", "error");
    return;
  }
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
    setMsg("auth-msg", "Erro: " + e.message, "error");
  }
}

async function doLogin() {
  const username = document.getElementById("auth-username").value.trim();
  const password = document.getElementById("auth-password").value;
  try {
    const res = await api("/api/auth/login", {
      method: "POST",
      body: { username, password },
    });
    setToken(res.token);
    setUser(res.user);
    afterLogin();
  } catch (e) {
    setMsg("auth-msg", "Erro: " + e.message, "error");
  }
}

function doLogout() {
  setToken(null);
  setUser(null);
  currentSessionId = null;
  currentSlug = null;
  currentGameState = null;
  readOnlyMode = false;
  document.getElementById("who").textContent = "";
  document.getElementById("logout-btn").style.display = "none";
  show("auth-screen");
}

function afterLogin() {
  const u = getUser();
  const tag = u && u.role === "admin" ? " (admin)" : "";
  document.getElementById("who").textContent = u ? `Logado: ${u.username}${tag}` : "";
  document.getElementById("logout-btn").style.display = "";
  loadLobby();
}

// --- Lobby: list saves ---
// Archived saves are opt-in: we only fetch ?archived=true and reveal the
// section when the user toggles it on (state held in `showArchived`).
let showArchived = false;

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
  if (advanced) advanced.style.display = isAdmin() ? "" : "none";
  // Admin panel entry is admin-only.
  const adminBlock = document.querySelector(".lobby-admin");
  if (adminBlock) adminBlock.style.display = isAdmin() ? "" : "none";
  const ul = document.getElementById("saves-list");
  ul.innerHTML = "";
  try {
    const active = await api(savesEndpoint(false));
    if (active.length === 0) {
      const li = document.createElement("li");
      li.innerHTML = '<span class="meta">Nenhum save ainda. Crie um novo jogo abaixo.</span>';
      ul.appendChild(li);
    } else {
      for (const s of active) ul.appendChild(renderSaveRow(s, { archived: false }));
    }
  } catch (e) {
    setMsg("lobby-msg", "Erro ao listar saves: " + e.message, "error");
  }
  // Archived section is rendered on demand by renderArchived().
  await renderArchived();
}

// Show/hide the archived section. When showing, fetch ?archived=true.
async function renderArchived() {
  const wrap = document.getElementById("archived-wrap");
  const ul = document.getElementById("archived-list");
  const toggle = document.getElementById("archive-toggle");
  ul.innerHTML = "";
  if (!showArchived) {
    wrap.style.display = "none";
    if (toggle) toggle.textContent = "Mostrar arquivados";
    return;
  }
  try {
    const archived = await api(savesEndpoint(true));
    for (const s of archived) ul.appendChild(renderSaveRow(s, { archived: true }));
  } catch (e) {
    setMsg("lobby-msg", "Erro ao listar arquivados: " + e.message, "error");
  }
  wrap.style.display = "";
  if (toggle) toggle.textContent = "Ocultar arquivados";
}

async function toggleArchived() {
  showArchived = !showArchived;
  await renderArchived();
}

// Build a single <li> row for a save. Own saves can be loaded and
// archived/restored. Admin rows for other users are read-only and can
// be deleted.
function renderSaveRow(s, { archived }) {
  const admin = isAdmin();
  const currentUser = getUser();
  const isOwnSave =
    !admin || !!(currentUser && Number(s.user_id) === Number(currentUser.id));
  const li = document.createElement("li");
  const meta = document.createElement("span");
  const owner = admin && s.username ? ` · <span class="owner">@${s.username}</span> ` : "";
  meta.innerHTML =
    `<span class="slug">${s.slug}</span>${owner}<span class="meta">${s.updated_at}</span>`;

  const loadBtn = document.createElement("button");
  if (admin && !isOwnSave) {
    loadBtn.textContent = "Visualizar";
    loadBtn.onclick = () => viewSaveReadOnly(s.user_id, s.slug);
  } else {
    loadBtn.textContent = "Carregar";
    loadBtn.onclick = () => loadSaveAsSession(s.slug);
  }

  li.appendChild(meta);
  if (isOwnSave) {
    const toggleBtn = document.createElement("button");
    toggleBtn.className = "secondary";
    if (archived) {
      toggleBtn.textContent = "Restaurar";
      toggleBtn.onclick = () => unarchiveSave(s.slug);
    } else {
      toggleBtn.textContent = "Arquivar";
      toggleBtn.onclick = () => archiveSave(s.slug);
    }
    li.appendChild(toggleBtn);
  }
  li.appendChild(loadBtn);

  // Admin-only delete (works on archived and non-archived alike).
  if (admin) {
    const delBtn = document.createElement("button");
    delBtn.className = "danger";
    delBtn.textContent = "Excluir";
    delBtn.onclick = () => deleteSaveAdmin(s.user_id, s.slug);
    li.appendChild(delBtn);
  }
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
  if (!confirm(`Excluir o save “${slug}” definitivamente?`)) return;
  try {
    await api(
      `/api/admin/saves/${encodeURIComponent(userId)}/${encodeURIComponent(slug)}`,
      { method: "DELETE" },
    );
    setMsg("lobby-msg", `“${slug}” excluído.`, "ok");
    await loadLobby();
  } catch (e) {
    setMsg("lobby-msg", "Erro ao excluir: " + e.message, "error");
  }
}

async function archiveSave(slug) {
  try {
    await api(`/api/saves/${encodeURIComponent(slug)}/archive`, { method: "POST" });
    setMsg("lobby-msg", `“${slug}” arquivado.`, "ok");
    await loadLobby();
  } catch (e) {
    setMsg("lobby-msg", "Erro ao arquivar: " + e.message, "error");
  }
}

async function unarchiveSave(slug) {
  try {
    await api(`/api/saves/${encodeURIComponent(slug)}/unarchive`, { method: "POST" });
    setMsg("lobby-msg", `“${slug}” restaurado.`, "ok");
    await loadLobby();
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
  modal.style.display = "flex";
  modal.dataset.characterId = player.id;
}

function closeASIModal() {
  const modal = document.getElementById("asi-modal");
  if (modal) modal.style.display = "none";
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
  // Reset which character sheet is visible — only persists within the
  // same game session (between /input calls). A new session always
  // opens on the player tab.
  activeSheetId = null;
  show("game-screen");
  clearLog();
  renderCharacterTools();

  if (readOnly) {
    // Disable all input controls; lobby button stays usable.
    document.getElementById("cmd").disabled = true;
    document.getElementById("send-btn").disabled = true;
    const rollBtn = document.getElementById("roll-btn");
    if (rollBtn) rollBtn.disabled = true;
    const rollCheck = document.getElementById("roll-check");
    if (rollCheck) rollCheck.disabled = true;
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
}

// Persist the current GameState to the user's save (Postgres). Best-effort.
async function persistSave(state) {
  if (!state) return;
  currentGameState = state;
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
    if (res.narration) appendLog("DM", res.narration, "narration");
    if (res.state) {
      currentGameState = res.state;
      updateLevelupBanner();
      checkPendingASI();
      await persistSave(res.state);
    }
  } catch (e) {
    if (e && e.status === 429) {
      appendLog("Sistema", LIMIT_REACHED_MSG, "system");
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

function renderCharacterTools() {
  const tools = document.getElementById("table-tools");
  if (!tools) return;
  const player = getPlayerCharacter();
  const party = (currentGameState && Array.isArray(currentGameState.party)) ? currentGameState.party : [];
  const companions = party.filter((c) => !c.is_player);
  const chars = player ? [player, ...companions] : companions;
  if (chars.length === 0) {
    tools.style.display = "none";
    return;
  }
  tools.style.display = "";
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
        `<button type="button" class="char-tab" role="tab" data-char-id="${escapeHtml(c.id)}" title="${escapeHtml(c.name)}">` +
        `<span class="tab-name">${escapeHtml(c.name)}</span>` +
        `<span class="tab-meta">${escapeHtml(c.race)} ${escapeHtml(cls)}${c.level ? ` ${c.level}` : ""}${escapeHtml(sub)}</span>` +
        `${isPlayerTag}` +
        `</button>`
      );
    })
    .join("");

  sheetHost.innerHTML = chars.map(renderSheetView).join("");

  // Keep the previously selected tab if its character is still in the
  // party, otherwise default to the player (or first available).
  const stillActive = chars.find((c) => c.id === activeSheetId);
  const target = stillActive || chars[0];
  setActiveSheetTab(target.id);

  for (const btn of tabsHost.querySelectorAll(".char-tab")) {
    btn.addEventListener("click", () => setActiveSheetTab(btn.dataset.charId));
  }
  updateRollPreview();
}

function setActiveSheetTab(charId) {
  activeSheetId = charId;
  const tabsHost = document.getElementById("char-tabs");
  const sheetHost = document.getElementById("sheet-host");
  if (!tabsHost || !sheetHost) return;
  for (const t of tabsHost.querySelectorAll(".char-tab")) {
    t.classList.toggle("active", t.dataset.charId === charId);
    t.setAttribute("aria-selected", t.dataset.charId === charId ? "true" : "false");
  }
  for (const v of sheetHost.querySelectorAll(".sheet-view")) {
    v.classList.toggle("active", v.dataset.charId === charId);
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
  const inventoryHtml = renderInventorySection(character.inventory);
  return (
    `<div class="sheet-view${isPlayer}" data-char-id="${escapeHtml(character.id)}">` +
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

// Phase 37: render the inventory list as a compact comma-separated
// line. Each item shows quantity (×N) when > 1, and magic items get a
// colored rarity dot. Returns "" if inventory is empty or missing.
function renderInventorySection(inventory) {
  if (!Array.isArray(inventory) || inventory.length === 0) return "";
  const items = inventory.map((it) => {
    const nm = escapeHtml(it.name || "—");
    const qty = Number(it.quantity || 0);
    const qStr = qty > 1 ? ` <span class="inv-qty">×${qty}</span>` : "";
    const magic = it.rarity && it.rarity !== "common"
      ? ` <span class="inv-magic" title="${escapeHtml(it.rarity)}">●</span>`
      : "";
    return `<span class="inv-item">${nm}${qStr}${magic}</span>`;
  });
  return `<div class="sheet-meta sheet-inv"><strong>Inventario:</strong> ${items.join(", ")}</div>`;
}

// Convert a spell slug ("magic-missile") or stored name ("fire bolt")
// into a Title Case display label ("Magic Missile" / "Fire Bolt").
// Stable for already-titled names.
function formatSpellName(s) {
  return escapeHtml(String(s || "")).replace(/(^|[\s-])\w/g, (c) => c.toUpperCase());
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
      await persistSave(res.state);
    }
    const r = res.result || {};
    if (r.error) {
      appendLog("Erro", r.error, "system");
    } else {
      if (r.narration) appendLog("DM", r.narration, "narration");
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
      appendLog("Sistema", LIMIT_REACHED_MSG, "system");
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
  loadLobby();
}

function openPlayGuide() {
  const guide = document.getElementById("play-guide");
  if (!guide) return;
  guide.open = true;
  guide.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function fillCommand(command) {
  const input = document.getElementById("cmd");
  if (!input || input.disabled) return;
  input.value = command;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
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

// --- Wire up events ---
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("login-btn").onclick = doLogin;
  document.getElementById("signup-btn").onclick = doSignup;
  document.getElementById("logout-btn").onclick = doLogout;
  document.getElementById("new-game-btn").onclick = createEmptySession;
  document.getElementById("wizard-btn").onclick = openWizard;
  document.getElementById("archive-toggle").onclick = toggleArchived;
  document.getElementById("send-btn").onclick = sendInput;
  document.getElementById("lobby-btn").onclick = returnToLobby;
  document.querySelectorAll(".command-chip").forEach((btn) => {
    btn.addEventListener("click", () => fillCommand(btn.dataset.command || ""));
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
  const adminBtn = document.getElementById("admin-panel-btn");
  if (adminBtn) adminBtn.onclick = openAdminPanel;
  const adminBack = document.getElementById("admin-back-btn");
  if (adminBack) adminBack.onclick = returnToLobby;
  const adminCreate = document.getElementById("admin-create-btn");
  if (adminCreate) adminCreate.onclick = openCreateUserModal;
  const adminRefresh = document.getElementById("admin-refresh-btn");
  if (adminRefresh) adminRefresh.onclick = loadAdminPanel;
  const adminQ = document.getElementById("admin-q");
  if (adminQ) adminQ.oninput = renderAdminUsers;
  const adminModalClose = document.getElementById("admin-modal-close");
  if (adminModalClose) adminModalClose.onclick = closeAdminModal;
  const adminDetailClose = document.getElementById("admin-detail-close");
  if (adminDetailClose) adminDetailClose.onclick = closeAdminDetail;
  document.getElementById("wz-prev").onclick = wizardPrev;
  document.getElementById("wz-next").onclick = wizardNext;
  document.getElementById("wz-finish").onclick = wizardFinish;
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
  const cmd = document.getElementById("cmd");
  cmd.addEventListener("keydown", (e) => {
    if (busy) return;
    if (e.key === "Enter") sendInput();
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
  root.innerHTML = cards
    .map(
      (c) =>
        `<div class="admin-card"><div class="admin-card-val">${c.value}</div>` +
        `<div class="admin-card-lbl">${c.label}</div></div>`,
    )
    .join("");
}

function renderAdminUsers() {
  const tbody = document.getElementById("admin-users-tbody");
  if (!tbody) return;
  const q = (document.getElementById("admin-q")?.value || "")
    .toLowerCase()
    .trim();
  const rows = adminUsers.filter(
    (u) => !q || u.username.toLowerCase().includes(q),
  );
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
      `<td><strong>${u.username}</strong><br><span class="meta">id ${u.id}</span></td>` +
      `<td>${u.role}</td>` +
      `<td>${status}</td>` +
      `<td>${u.tokens_today.toLocaleString("pt-BR")} / ${limit}</td>` +
      `<td>$${Number(u.cost_month || 0).toFixed(4)}</td>` +
      `<td class="admin-actions"></td>`;
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
    tbody.innerHTML = '<tr><td colspan="6" class="meta">Nenhum usuário.</td></tr>';
  }
}

function adminMiniBtn(label, onclick, cls) {
  const b = document.createElement("button");
  b.textContent = label;
  b.className = "mini " + (cls || "secondary");
  b.onclick = onclick;
  return b;
}

// --- Modal plumbing ---

function closeAdminModal() {
  document.getElementById("admin-modal").style.display = "none";
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
  document.getElementById("admin-modal").style.display = "";
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
  document.getElementById("admin-modal").style.display = "";
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
  document.getElementById("admin-modal").style.display = "";
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
  if (!confirm(`Confirmar ${verb} "${u.username}"?`)) return;
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
  if (!confirm(`Excluir "${u.username}" (id ${u.id})? Isto apaga saves e histórico.`))
    return;
  if (!confirm(`Tem certeza? Esta ação é irreversível.`)) return;
  try {
    await api(`/api/admin/users/${u.id}`, { method: "DELETE" });
    await loadAdminPanel();
    setMsg("admin-msg", `"${u.username}" excluído.`, "ok");
  } catch (e) {
    setMsg("admin-msg", "Erro: " + e.message, "error");
  }
}

function closeAdminDetail() {
  document.getElementById("admin-detail").style.display = "none";
}

async function loadUserDetail(id, username) {
  document.getElementById("admin-detail-title").textContent = `${username} — atividade`;
  document.getElementById("admin-activity-list").innerHTML =
    '<li class="meta">Carregando…</li>';
  document.getElementById("admin-usage-list").innerHTML =
    '<li class="meta">Carregando…</li>';
  document.getElementById("admin-detail").style.display = "";
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
  // Fetch catalog first.
  setMsg("lobby-msg", "Carregando...", "");
  try {
    wizardState.options = await api("/api/character-options");
    wizardState.step = 0;
    // Companions start empty; renderWizardCompanions rolls 4 candidates
    // against the player's class the first time step 10 is reached.
    wizardState.companions = [];
    wizardState.companionCandidates = null;
    wizardState.spell_selection = emptySpellSelection();
    show("wizard-screen");
    renderWizardStep();
    setMsg("lobby-msg", "", "");
  } catch (e) {
    setMsg("lobby-msg", "Erro ao carregar opções: " + e.message, "error");
  }
}

function renderWizardStep() {
  // Hide all, show current.
  for (let i = 1; i <= WIZARD_STEPS.length; i++) {
    const el = document.getElementById(`wizard-step-${i}`);
    if (el) el.classList.toggle("active", i === wizardState.step + 1);
  }
  // Progress dots.
  const prog = document.getElementById("wizard-progress");
  prog.innerHTML = "";
  for (let i = 0; i < WIZARD_STEPS.length; i++) {
    const dot = document.createElement("div");
    dot.className = "dot" + (i < wizardState.step ? " done" : "") + (i === wizardState.step ? " active" : "");
    prog.appendChild(dot);
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
  setMsg("wizard-msg", "", "");
}

// --- Step renderers ---

function renderWizardName() {
  document.getElementById("wz-campaign-name").value = wizardState.campaign_name;
  document.getElementById("wz-char-name").value = wizardState.name;
  document.getElementById("wz-campaign-name").oninput = (e) => {
    wizardState.campaign_name = e.target.value;
  };
  document.getElementById("wz-char-name").oninput = (e) => {
    wizardState.name = e.target.value;
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
    const card = document.createElement("div");
    card.className = "choice" + (wizardState.race === r.name ? " selected" : "");
    card.innerHTML = `<div class="name">${r.name}</div>
      <div class="desc">${r.size} · ${r.speed} ft${r.subraces.length ? ` · ${r.subraces.length} sub-raças` : ""}</div>`;
    card.onclick = () => {
      wizardState.race = r.name;
      wizardState.subrace = null;
      renderWizardStep();
    };
    root.appendChild(card);
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
      sel.onchange = (e) => { wizardState.subrace = e.target.value || null; };
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
    const card = document.createElement("div");
    card.className = "choice" + (wizardState.char_class === c.name ? " selected" : "");
    card.innerHTML = `<div class="name">${c.name}</div>
      <div class="desc">${c.hit_dice} · ${c.num_skill_choices} perícias${c.is_spellcaster ? " · spellcaster" : ""}</div>`;
    card.onclick = () => {
      wizardState.char_class = c.name;
      wizardState.subclass = null;
      wizardState.skills = [];
      wizardState.spell_selection = emptySpellSelection();
      wizardState.companions = [];
      wizardState.companionCandidates = null;
      renderWizardStep();
    };
    root.appendChild(card);
  }
}

function renderWizardSubclass() {
  const root = document.getElementById("wz-subclasses");
  root.innerHTML = "";
  if (!wizardState.char_class) {
    root.innerHTML = '<div class="msg">Escolha uma classe primeiro.</div>';
    return;
  }
  const cls = wizardState.options.classes.find((c) => c.name === wizardState.char_class);
  if (!cls || !cls.subclasses.length) {
    root.innerHTML = '<div class="msg">Esta classe não tem subclasses no PHB.</div>';
    return;
  }
  for (const s of cls.subclasses) {
    const card = document.createElement("div");
    card.className = "choice" + (wizardState.subclass === s ? " selected" : "");
    card.innerHTML = `<div class="name">${s}</div>`;
    card.onclick = () => {
      wizardState.subclass = wizardState.subclass === s ? null : s;
      wizardState.companions = [];
      wizardState.companionCandidates = null;
      renderWizardStep();
    };
    root.appendChild(card);
  }
}

function renderWizardBackground() {
  const root = document.getElementById("wz-backgrounds");
  root.innerHTML = "";
  for (const b of wizardState.options.backgrounds) {
    const card = document.createElement("div");
    card.className = "choice" + (wizardState.background === b.name ? " selected" : "");
    card.innerHTML = `<div class="name">${b.name}</div>
      <div class="desc">${b.feature || ""}</div>`;
    card.onclick = () => {
      wizardState.background = b.name;
      renderWizardStep();
    };
    root.appendChild(card);
  }
}

function renderWizardAlignment() {
  const root = document.getElementById("wz-alignments");
  root.innerHTML = "";
  for (const a of wizardState.options.alignments) {
    const card = document.createElement("div");
    card.className = "choice" + (wizardState.alignment === a ? " selected" : "");
    card.innerHTML = `<div class="name">${a}</div>`;
    card.onclick = () => {
      wizardState.alignment = a;
      renderWizardStep();
    };
    root.appendChild(card);
  }
}

function renderWizardLevel() {
  const root = document.getElementById("wz-levels");
  root.innerHTML = "";
  for (const lv of wizardState.options.levels) {
    const card = document.createElement("div");
    card.className = "choice" + (wizardState.level === lv ? " selected" : "");
    card.innerHTML = `<div class="name">Nível ${lv}</div>`;
    card.onclick = () => {
      wizardState.level = lv;
      wizardState.spell_selection = emptySpellSelection();
      renderWizardStep();
    };
    root.appendChild(card);
  }
}

function renderWizardStats() {
  const root = document.getElementById("wz-stats-methods");
  root.innerHTML = "";
  for (const m of wizardState.options.stats_methods) {
    const card = document.createElement("div");
    card.className = "choice" + (wizardState.stats_method === m.id ? " selected" : "");
    card.innerHTML = `<div class="name">${m.label}</div>`;
    card.onclick = () => {
      wizardState.stats_method = m.id;
      renderWizardStep();
    };
    root.appendChild(card);
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
    empty.className = "msg";
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
        renderWizardCompanions();
      })
      .catch((e) => {
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
  const summary = [
    ["Campanha", wizardState.campaign_name || "(vazio)"],
    ["Narração", wizardState.narration_length],
    [
      "Cenário inicial",
      wizardState.initial_scenario
        ? (wizardState.initial_scenario.length > 80
            ? wizardState.initial_scenario.slice(0, 79) + "…"
            : wizardState.initial_scenario)
        : "(mestre decide)",
    ],
    ["Personagem", wizardState.name || "(vazio)"],
    ["Raça", wizardState.race + (wizardState.subrace ? ` (${wizardState.subrace})` : "")],
    ["Classe", wizardState.char_class + (wizardState.subclass ? ` (${wizardState.subclass})` : "")],
    ["Background", wizardState.background],
    ["Alinhamento", wizardState.alignment],
    ["Nível", wizardState.level],
    ["Método de atributos", wizardState.stats_method],
    ["Perícias", wizardState.skills.join(", ") || "(nenhuma)"],
    ["Truques", wizardState.spell_selection.cantrips.join(", ") || "(nenhum)"],
    ["Magias", wizardSpellSummary()],
    ["Companheiros", wizardState.companions.length
      ? wizardState.companions.map((k) => wizardState.options.companions.find((c) => c.key === k)?.name || k).join(", ")
      : "(solo)"],
  ];
  const block = document.createElement("div");
  block.className = "summary-block";
  for (const [k, v] of summary) {
    const r = document.createElement("div");
    r.className = "row";
    const ke = document.createElement("div");
    ke.className = "key";
    ke.textContent = k;
    const ve = document.createElement("div");
    ve.className = "val";
    ve.textContent = v;
    r.appendChild(ke);
    r.appendChild(ve);
    block.appendChild(r);
  }
  root.innerHTML = "";
  root.appendChild(block);
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
    wizardState.step--;
    renderWizardStep();
  }
}

function wizardNext() {
  const err = wizardValidateStep(wizardState.step);
  if (err) {
    setMsg("wizard-msg", err, "error");
    return;
  }
  if (wizardState.step < WIZARD_STEPS.length - 1) {
    wizardState.step++;
    renderWizardStep();
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
  }
}
