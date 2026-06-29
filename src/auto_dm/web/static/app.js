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
//   POST   /api/sessions/{sid}/stream {line}               -> text/event-stream
//   DELETE /api/sessions/{sid}                             -> 204

const API_BASE = ""; // same origin (Vercel would be cross-origin, but
                    // for the dev server, same origin works)

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
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch (_) {}
    throw new Error(detail);
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

// --- Phase 28: input-blocking + busy feedback ---
let busy = false;

function lockUi() {
  if (busy) return;
  busy = true;
  document.getElementById("cmd").disabled = true;
  document.getElementById("send-btn").disabled = true;
  const toggle = document.getElementById("stream-toggle");
  if (toggle) toggle.disabled = true;
}

function unlockUi() {
  busy = false;
  document.getElementById("cmd").disabled = false;
  document.getElementById("send-btn").disabled = false;
  const toggle = document.getElementById("stream-toggle");
  if (toggle) toggle.disabled = false;
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
  document.getElementById("who").textContent = "";
  document.getElementById("logout-btn").style.display = "none";
  show("auth-screen");
}

function afterLogin() {
  const u = getUser();
  document.getElementById("who").textContent = u ? `Logado: ${u.username}` : "";
  document.getElementById("logout-btn").style.display = "";
  loadLobby();
}

// --- Lobby: list saves ---
async function loadLobby() {
  show("lobby-screen");
  const ul = document.getElementById("saves-list");
  ul.innerHTML = "";
  try {
    const saves = await api("/api/saves");
    if (saves.length === 0) {
      const li = document.createElement("li");
      li.innerHTML = '<span class="meta">Nenhum save ainda. Crie um novo jogo abaixo.</span>';
      ul.appendChild(li);
      return;
    }
    for (const s of saves) {
      const li = document.createElement("li");
      const meta = document.createElement("span");
      meta.innerHTML = `<span class="slug">${s.slug}</span> <span class="meta">${s.updated_at}</span>`;
      const btn = document.createElement("button");
      btn.textContent = "Carregar";
      btn.onclick = () => loadSaveAsSession(s.slug);
      li.appendChild(meta);
      li.appendChild(btn);
      ul.appendChild(li);
    }
  } catch (e) {
    setMsg("lobby-msg", "Erro ao listar saves: " + e.message, "error");
  }
}

async function loadSaveAsSession(slug) {
  try {
    const res = await api(`/api/saves/${encodeURIComponent(slug)}/load`, {
      method: "POST",
    });
    currentSessionId = res.session_id;
    currentSlug = slug;
    enterGame();
  } catch (e) {
    setMsg("lobby-msg", "Erro: " + e.message, "error");
  }
}

async function createEmptySession() {
  const slug = document.getElementById("new-slug").value.trim() || "untitled";
  // Phase 26a: create a minimal GameState with a placeholder character.
  // Full character creation is in 26c (wizard).
  const state = {
    campaign_name: slug,
    current_location: "Taverna do Javali Dourado",
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
    // Persist the empty state as a save.
    await api("/api/saves", {
      method: "POST",
      body: { slug, state: res.state },
    });
    enterGame();
  } catch (e) {
    setMsg("lobby-msg", "Erro: " + e.message, "error");
  }
}

// --- Game screen ---
function enterGame() {
  show("game-screen");
  clearLog();
  appendLog("Sistema",
    `Sessão iniciada${currentSlug ? ` (save: ${currentSlug})` : ""}.`,
    "system");
  if (currentSlug) {
    appendLog("Sistema",
      "Personagem vazio — em breve o wizard de criação vai abrir automaticamente. " +
      "Por enquanto, digite algo para começar (a IA narrará mesmo sem personagem).",
      "system");
  }
}

async function sendInput() {
  if (busy) return;
  const input = document.getElementById("cmd");
  const line = input.value.trim();
  if (!line || !currentSessionId) return;
  input.value = "";
  appendLog("Você", line, "player");
  lockUi();
  showTyping();
  const useStream = !!document.getElementById("stream-toggle")?.checked;
  try {
    if (useStream) {
      await sendInputStream(line);
    } else {
      await sendInputClassic(line);
    }
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
    appendLog("Erro", e.message, "system");
  }
}

// --- Streaming narration via SSE (Phase 26b) ---
//
// `fetch` (not `EventSource`) is used because we need POST + the
// Authorization header. The response body is read as a stream and
// each SSE `data: {json}\n\n` chunk is parsed and appended live.
async function sendInputStream(line) {
  // Create an in-progress entry we'll append tokens into.
  const out = output();
  const entry = document.createElement("div");
  entry.className = "entry narration";
  const w = document.createElement("span");
  w.className = "who";
  w.textContent = "DM";
  const b = document.createElement("span");
  b.className = "body";
  b.textContent = "";
  entry.appendChild(w);
  entry.appendChild(b);
  out.appendChild(entry);
  out.scrollTop = out.scrollHeight;

  try {
    const tok = getToken();
    const res = await fetch(`/api/sessions/${currentSessionId}/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
      },
      body: JSON.stringify({ line }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = await res.json();
        detail = j.detail || JSON.stringify(j);
      } catch (_) {}
      b.textContent = `(erro ${res.status}: ${detail})`;
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE events are separated by a blank line (\n\n).
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        // raw may be one or more `data: <line>` lines.
        const dataLines = raw
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trim());
        if (!dataLines.length) continue;
        const payload = dataLines.join("\n");
        try {
          const evt = JSON.parse(payload);
          if (evt.type === "token") {
            b.textContent = (b.textContent || "") + (evt.data || "");
            out.scrollTop = out.scrollHeight;
          } else if (evt.type === "error") {
            b.textContent += `\n[erro: ${evt.data}]`;
          } else if (evt.type === "done") {
            // Stream complete — leave b.textContent as-is.
          }
        } catch (_) {
          // Not JSON; ignore.
        }
      }
    }
  } catch (e) {
    b.textContent = (b.textContent || "") + `\n[falha: ${e.message}]`;
  }
}

// --- /command helpers (client-side) ---
async function clientCommand(line) {
  // /quit, /save, /load, /list, /help — handled locally.
  const parts = line.trim().split(/\s+/);
  const cmd = parts[0].toLowerCase();
  if (cmd === "/help") {
    appendLog("Sistema",
      "Comandos: /help /status /look /inventory /conditions /spells " +
      "/encounter <mon> /save [slug] /load <slug> /list /quit",
      "system");
    return true;
  }
  if (cmd === "/quit") {
    appendLog("Sistema", "Voltando ao lobby...", "system");
    currentSessionId = null;
    loadLobby();
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
  return false;
}

// --- Wire up events ---
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("login-btn").onclick = doLogin;
  document.getElementById("signup-btn").onclick = doSignup;
  document.getElementById("logout-btn").onclick = doLogout;
  document.getElementById("new-game-btn").onclick = createEmptySession;
  document.getElementById("wizard-btn").onclick = openWizard;
  document.getElementById("send-btn").onclick = sendInput;
  document.getElementById("wz-prev").onclick = wizardPrev;
  document.getElementById("wz-next").onclick = wizardNext;
  document.getElementById("wz-finish").onclick = wizardFinish;
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
// Wizard (Phase 26c)
// ============================================================================

const WIZARD_STEPS = [
  "name", "race", "class", "subclass",
  "background", "alignment", "level", "stats",
  "skills", "companions", "confirm",
];

let wizardState = {
  step: 0,             // index into WIZARD_STEPS
  options: null,        // loaded catalog from /api/character-options
  campaign_name: "",
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
  companions: [],
  // Phase 27: companions are rolled lazily from /api/companions/roll when
  // the player reaches step 10 (synergy-biased against wizardState.char_class).
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
    show("wizard-screen");
    renderWizardStep();
    setMsg("lobby-msg", "", "");
  } catch (e) {
    setMsg("lobby-msg", "Erro ao carregar opções: " + e.message, "error");
  }
}

function renderWizardStep() {
  // Hide all, show current.
  for (let i = 1; i <= 11; i++) {
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
    ["Personagem", wizardState.name || "(vazio)"],
    ["Raça", wizardState.race + (wizardState.subrace ? ` (${wizardState.subrace})` : "")],
    ["Classe", wizardState.char_class + (wizardState.subclass ? ` (${wizardState.subclass})` : "")],
    ["Background", wizardState.background],
    ["Alinhamento", wizardState.alignment],
    ["Nível", wizardState.level],
    ["Método de atributos", wizardState.stats_method],
    ["Perícias", wizardState.skills.join(", ") || "(nenhuma)"],
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
    const res = await api("/api/sessions/with-character", {
      method: "POST",
      body: payload,
    });
    currentSessionId = res.session_id;
    currentSlug = res.slug;
    // Auto-save.
    await api("/api/saves", {
      method: "POST",
      body: { slug: res.slug, state: res.state },
    });
    enterGame();
    appendLog("Sistema",
      `Campanha "${res.slug}" criada com personagem ${wizardState.name}!`,
      "system");
  } catch (e) {
    setMsg("wizard-msg", "Erro: " + e.message, "error");
  } finally {
    btn.disabled = false;
  }
}
