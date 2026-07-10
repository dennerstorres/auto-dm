import { test, expect, login, mockApi } from "./fixtures.js";

test("referências públicas: landing e login", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page).toHaveScreenshot("landing.png", { fullPage: true });
  await page.locator("#hero-login").click();
  await expect(page).toHaveScreenshot("login.png", { fullPage: true });
});

test("referências autenticadas: lobby, wizard e jogo", async ({ page }) => {
  await login(page);
  await expect(page).toHaveScreenshot("lobby.png", { fullPage: true });
  await page.locator("#wizard-btn").click();
  await expect(page.locator("#wizard-workspace")).toBeVisible();
  await expect(page).toHaveScreenshot("wizard.png", { fullPage: true });
  await page.locator("#shell-lobby-btn").click();
  await page.getByRole("button", { name: "Continuar aventura" }).click();
  await expect(page).toHaveScreenshot("game.png", { fullPage: true });
});

test("referência administrativa", async ({ page }) => {
  await login(page, { role: "admin" });
  await page.locator("#shell-admin-btn").click();
  await expect(page.locator("#admin-users-tbody tr")).toHaveCount(2);
  await expect(page).toHaveScreenshot("admin.png", { fullPage: true });
});
