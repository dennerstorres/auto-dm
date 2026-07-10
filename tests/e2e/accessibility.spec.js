import AxeBuilder from "@axe-core/playwright";
import { test, expect, login, mockApi } from "./fixtures.js";

async function expectNoCriticalViolations(page, label) {
  const result = await new AxeBuilder({ page }).analyze();
  const critical = result.violations.filter((item) => item.impact === "critical");
  expect(critical, `${label}: ${critical.map((item) => item.id).join(", ")}`).toEqual([]);
}

test("landing e autenticação não têm violações críticas", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expectNoCriticalViolations(page, "landing");
  await page.locator("#hero-login").click();
  await expectNoCriticalViolations(page, "login");
});

test("lobby, wizard e jogo não têm violações críticas", async ({ page }) => {
  await login(page);
  await expectNoCriticalViolations(page, "lobby");
  await page.locator("#wizard-btn").click();
  await expect(page.locator("#wizard-workspace")).toBeVisible();
  await expectNoCriticalViolations(page, "wizard");
  await page.locator("#shell-lobby-btn").click();
  await page.getByRole("button", { name: "Continuar aventura" }).click();
  await expectNoCriticalViolations(page, "jogo");
});

test("admin não tem violações críticas", async ({ page }) => {
  await login(page, { role: "admin" });
  await page.locator("#shell-admin-btn").click();
  await expect(page.locator("#admin-users-tbody tr")).toHaveCount(2);
  await expectNoCriticalViolations(page, "admin");
});
