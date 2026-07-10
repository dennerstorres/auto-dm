import { test, expect, login, mockApi } from "./fixtures.js";

test("login leva o jogador ao lobby", async ({ page }) => {
  await login(page);
  await expect(page.locator("#who")).toHaveText("@aventureira");
  await expect(page.getByRole("button", { name: "Continuar aventura" })).toBeVisible();
});

test("cadastro cria sessão autenticada", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.locator("#hero-signup").click();
  await page.locator("#auth-username").fill("nova_heroina");
  await page.locator("#auth-password").fill("senha-segura");
  await page.locator("#signup-btn").click();
  await expect(page.locator("#lobby-screen")).toBeVisible();
});

test("continuar save restaura narrativa e mesa", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "Continuar aventura" }).click();
  await expect(page.locator("#game-screen")).toBeVisible();
  await expect(page.locator("#output")).toContainText("A névoa se abre");
  await expect(page.locator("#game-campaign-name")).toHaveText("As Ruínas de Umbra");
});

test("criação de personagem percorre o wizard e abre o jogo", async ({ page }) => {
  await login(page);
  await page.locator("#wizard-btn").click();
  await expect(page.locator("#wizard-workspace")).toBeVisible();

  await page.locator("#wz-campaign-name").fill("Nova Jornada");
  await page.locator("#wz-char-name").fill("Ayla");
  await page.locator("#wz-next").click();
  await page.locator("#wz-races .choice").first().click();
  await page.locator("#wz-next").click();
  await page.locator("#wz-classes .choice").first().click();
  await page.locator("#wz-next").click();
  await page.locator("#wz-subclasses .choice").first().click();
  await page.locator("#wz-next").click();
  await page.locator("#wz-backgrounds .choice").first().click();
  await page.locator("#wz-next").click();
  await page.locator("#wz-alignments .choice").first().click();
  await page.locator("#wz-next").click();
  await page.locator("#wz-levels .choice", { hasText: "Nível 1" }).click();
  await page.locator("#wz-next").click();
  await page.locator("#wz-stats-methods .choice").first().click();
  await page.locator("#wz-next").click();
  await page.locator("#wz-next").click();
  await page.locator("#wz-next").click();
  await page.locator("#wz-next").click();
  await page.locator("#wz-finish").click();

  await expect(page.locator("#game-screen")).toBeVisible();
  await expect(page.locator("#output")).toContainText("Campanha \"nova-jornada\" criada");
});
