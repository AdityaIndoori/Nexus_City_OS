/* Full-page renders of /landing at desktop + mobile widths for review. */
const path = require("path");
const os = require("os");
const puppeteer = require(path.join(
  os.tmpdir(), "nexus-shots", "node_modules", "puppeteer-core"));
const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const OUT = os.tmpdir();
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: "new",
    args: ["--hide-scrollbars"],
  });
  for (const [name, w, h] of [["landing-desktop", 1440, 900],
                              ["landing-mobile", 390, 844]]) {
    const page = await browser.newPage();
    await page.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
    await page.goto("http://127.0.0.1:8899/landing",
                    { waitUntil: "networkidle2", timeout: 60000 });
    await sleep(1500);
    await page.screenshot({ path: path.join(OUT, name + ".png"),
                            fullPage: true });
    console.log(path.join(OUT, name + ".png"));
    await page.close();
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });