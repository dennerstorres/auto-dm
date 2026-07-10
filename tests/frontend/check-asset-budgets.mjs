import { readFile, stat } from "node:fs/promises";
import path from "node:path";

const root = path.resolve("src/auto_dm/web/static");
const html = await readFile(path.join(root, "index.html"), "utf8");

const stylesheetPaths = [...html.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"?]+)/g)]
  .map((match) => match[1]);
const modulePaths = ["/app.js", "/shell.js"];

async function totalBytes(paths) {
  const sizes = await Promise.all(paths.map(async (asset) => {
    const info = await stat(path.join(root, asset.replace(/^\//, "")));
    return info.size;
  }));
  return sizes.reduce((total, size) => total + size, 0);
}

const budgets = [
  { name: "CSS inicial", actual: await totalBytes(stylesheetPaths), limit: 130 * 1024 },
  { name: "JavaScript inicial", actual: await totalBytes(modulePaths), limit: 165 * 1024 },
  {
    name: "Hero AVIF",
    actual: (await stat(path.join(root, "assets/hero-party-dragon.avif"))).size,
    limit: 220 * 1024,
  },
  {
    name: "Hero WebP",
    actual: (await stat(path.join(root, "assets/hero-party-dragon.webp"))).size,
    limit: 300 * 1024,
  },
  {
    name: "Fallback hero PNG",
    actual: (await stat(path.join(root, "assets/hero-party-dragon.png"))).size,
    limit: 900 * 1024,
  },
];

let failed = false;
for (const budget of budgets) {
  const kib = (budget.actual / 1024).toFixed(1);
  const limitKib = (budget.limit / 1024).toFixed(0);
  const ok = budget.actual <= budget.limit;
  console.log(`${ok ? "PASS" : "FAIL"} ${budget.name}: ${kib} KiB / ${limitKib} KiB`);
  failed ||= !ok;
}

if (failed) process.exitCode = 1;
