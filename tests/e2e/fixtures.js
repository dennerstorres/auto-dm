import { test as base, expect } from "@playwright/test";

export const user = { id: 7, username: "aventureira", role: "user" };
export const admin = { id: 1, username: "rootadmin", role: "admin" };

export const gameState = {
  campaign_name: "As Ruínas de Umbra",
  current_location: "Portões da cidadela",
  player_character_id: "hero-1",
  party_xp: 900,
  in_combat: false,
  narrative_log: [
    { role: "dm", speaker: "DM", content: "A névoa se abre diante dos portões antigos." },
    { role: "player", speaker: "Ayla", content: "Eu examino as runas antes de avançar." },
  ],
  party: [{
    id: "hero-1", name: "Ayla", is_player: true, race: "Humano", char_class: "Fighter",
    level: 3, hp_current: 24, hp_max: 28, armor_class: 17, proficiency_bonus: 2,
    abilities: { strength: 16, dexterity: 14, constitution: 14, intelligence: 10, wisdom: 12, charisma: 8 },
    proficiencies: { skills: ["athletics", "perception"], saves: ["strength", "constitution"] },
    conditions: [], inventory: [],
  }],
};

const save = {
  slug: "ruinas-de-umbra", campaign_name: "As Ruínas de Umbra", character_name: "Ayla",
  character_level: 3, current_location: "Portões da cidadela",
  updated_at: "2026-07-10T12:00:00Z", archived: false,
};

const characterOptions = {
  races: [{ name: "Humano", size: "Médio", speed: 30, subraces: [] }],
  classes: [{
    name: "Fighter", hit_dice: "d10", num_skill_choices: 2, is_spellcaster: false,
    subclasses: ["Champion"], skill_options: ["athletics", "perception"], spellcasting: null,
  }],
  backgrounds: [{ name: "Soldado", feature: "Patente militar" }],
  alignments: ["Neutro e Bom"],
  levels: [1, 3],
  stats_methods: [{ id: "standard_array", label: "Valores padrão" }],
  companions: [],
};

async function json(route, body, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export async function mockApi(page, { role = "user", saves = [save] } = {}) {
  const currentUser = role === "admin" ? admin : user;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname === "/api/auth/login" || pathname === "/api/auth/signup") {
      return json(route, { token: "e2e-token", user: currentUser }, pathname.endsWith("signup") ? 201 : 200);
    }
    if (pathname === "/api/auth/me") return json(route, currentUser);
    if (pathname === "/api/saves" && request.method() === "GET") return json(route, saves);
    if (pathname === "/api/saves" && request.method() === "POST") return json(route, save, 201);
    if (pathname === "/api/saves/ruinas-de-umbra/load") {
      return json(route, { session_id: "session-1", state: gameState });
    }
    if (pathname === "/api/character-options") return json(route, characterOptions);
    if (pathname === "/api/companions/roll") return json(route, { companions: [] });
    if (pathname === "/api/sessions/with-character") {
      return json(route, { session_id: "session-new", slug: "nova-jornada", state: gameState }, 201);
    }
    if (pathname.endsWith("/opening")) {
      return json(route, { narration: "A aventura começa sob um céu cor de âmbar.", state: gameState });
    }
    if (pathname === "/api/admin/users") {
      return json(route, [
        { id: 7, username: "aventureira", role: "user", active: true, unlimited: false, tokens_today: 1280, cost_month: 0.034 },
        { id: 8, username: "bardo", role: "user", active: false, unlimited: false, tokens_today: 0, cost_month: 0.012 },
      ]);
    }
    if (pathname === "/api/admin/usage/summary") {
      return json(route, { cost_usd: 1.2345, tokens: 42000, active_users: 6, disabled_users: 1 });
    }
    if (pathname === "/api/admin/saves") return json(route, saves.map((item) => ({ ...item, user_id: 1, username: "rootadmin" })));
    if (pathname === "/api/me/preferences") {
      return json(route, { tts: { enabled: false, auto_play: false, voice: "pt-BR-FranciscaNeural", rate: "+0%" }, music: { enabled: false, src: "", volume: 0.4 } });
    }
    return json(route, { detail: `Mock ausente: ${request.method()} ${pathname}` }, 404);
  });
}

export async function login(page, { role = "user" } = {}) {
  await mockApi(page, { role });
  await page.goto("/");
  await page.getByRole("button", { name: "Já tenho uma conta" }).click();
  await page.locator("#auth-username").fill(role === "admin" ? "rootadmin" : "aventureira");
  await page.locator("#auth-password").fill("senha-segura");
  await page.locator("#login-btn").click();
  await expect(page.locator("#lobby-screen")).toBeVisible();
}

export const test = base;
export { expect };
