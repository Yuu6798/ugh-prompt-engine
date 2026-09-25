// Playwright レンダー。
//   node render.mjs stills out_dir t1 t2 ...   各時刻の静止フレーム（PNG）
//   node render.mjs frames out_dir [workers]  240fps サブフレーム（1 出力フレーム = 4 サブフレーム）
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "/opt/node22/lib/node_modules/playwright");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const URL = "file://" + path.join(HERE, "index.html");
const [mode, outDir, ...rest] = process.argv.slice(2);
mkdirSync(outDir, { recursive: true });

async function openPage(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1440 }, deviceScaleFactor: 1 });
  await page.goto(URL);
  await page.waitForFunction(() => window.READY === true, null, { timeout: 60000 });
  return page;
}

const browser = await chromium.launch({ args: ["--allow-file-access-from-files"] });
if (mode === "stills") {
  const page = await openPage(browser);
  for (const ts of rest) {
    await page.evaluate((t) => window.seek(t), parseFloat(ts));
    await page.screenshot({ path: path.join(outDir, `t${parseFloat(ts).toFixed(2).padStart(6, "0")}.png`) });
  }
} else if (mode === "frames") {
  const workers = parseInt(rest[0] || "4", 10);
  const total = await (await openPage(browser)).evaluate(() => window.DATA.total);
  const n = Math.round(total * 60) * 4;
  const pages = await Promise.all(Array.from({ length: workers }, () => openPage(browser)));
  let next = 0;
  const t0 = Date.now();
  await Promise.all(pages.map(async (page) => {
    for (;;) {
      const i = next++;
      if (i >= n) break;
      const t = (i - 1.5) / 240; // 出力フレーム k の 4 サブフレームは k/60 を中心に並ぶ
      await page.evaluate((tt) => window.seek(tt), t);
      await page.screenshot({
        path: path.join(outDir, `${String(i).padStart(5, "0")}.jpg`), type: "jpeg", quality: 94,
      });
      if (i % 480 === 0) console.log(`${i}/${n} ${((Date.now() - t0) / 1000).toFixed(0)}s`);
    }
  }));
}
await browser.close();
