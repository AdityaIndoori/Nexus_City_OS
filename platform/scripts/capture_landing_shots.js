/* Capture real-system screenshots for the landing page.
   Run with: node platform/scripts/capture_landing_shots.js
   Requires: puppeteer-core installed at %TEMP%\nexus-shots, a sim server
   on 127.0.0.1:8899 with NEXUS_DEMO_PREFILL=1 and injected incidents. */
const path = require("path");
const os = require("os");
const puppeteer = require(path.join(
  os.tmpdir(), "nexus-shots", "node_modules", "puppeteer-core"));

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const BASE = "http://127.0.0.1:8899";
const OUT = path.resolve(__dirname, "..", "ui", "landing-assets");

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const fs = require("fs");
  fs.mkdirSync(OUT, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--window-size=1600,1000", "--hide-scrollbars"],
    defaultViewport: { width: 1600, height: 1000, deviceScaleFactor: 1.5 },
  });
  const page = await browser.newPage();

  // login
  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 60000 });
  await page.waitForSelector("#li_user", { visible: true });
  await page.click("#loginbox button.primary");
  await page.waitForSelector("#loginoverlay", { hidden: true, timeout: 20000 });

  // let the map tiles + queue + SSE settle
  await sleep(9000);

  // 1) full console (triage inbox + map + bottom panels)
  await page.screenshot({ path: path.join(OUT, "console.png") });
  console.log("console.png");

  // 2) incident workspace: open the first critical incident row
  const opened = await page.evaluate(() => {
    const row = document.querySelector('#incidents .trow[data-key^="inc:"]');
    if (!row) return false;
    row.click();
    return true;
  });
  if (opened) {
    await sleep(2500);
    // type some operator notes so the autosave indicator shows
    const ta = await page.$(".notesblock textarea");
    if (ta) {
      await ta.click();
      await page.keyboard.type(
        "SPD unit dispatched; NB lanes blocked at cross street. " +
        "Signal retiming plan pending review.");
      await sleep(2500);   // debounce + save
    }
    const rail = await page.$("#sidepanel");
    await rail.screenshot({ path: path.join(OUT, "workspace.png") });
    console.log("workspace.png");

    // 3) governance block: request a recommendation if not present
    const hasPlan = await page.evaluate(() => !!document.querySelector(".gov"));
    if (!hasPlan) {
      await page.evaluate(() => {
        const b = [...document.querySelectorAll("#incidents button")]
          .find(x => x.textContent.includes("RECOMMENDATION"));
        if (b) b.click();
      });
      await sleep(5000);
    }
    const gov = await page.$("#incidents .gov");
    if (gov) {
      await gov.screenshot({ path: path.join(OUT, "governance.png") });
      console.log("governance.png");
    }
    await page.evaluate(() => window.backToTriage());
    await sleep(1200);
  }

  // 4) triage inbox close-up (right rail)
  const rail2 = await page.$("#sidepanel");
  await rail2.screenshot({ path: path.join(OUT, "triage.png") });
  console.log("triage.png");

  // 5) analytics tab
  await page.evaluate(() => window.setPanelTab("analytics"));
  await sleep(3500);
  const ap = await page.$("#bottompanels section.panel");
  await ap.screenshot({ path: path.join(OUT, "analytics.png") });
  console.log("analytics.png");

  // 6) audit chain panel
  const panels = await page.$$("#bottompanels section.panel");
  if (panels[2]) {
    await panels[2].screenshot({ path: path.join(OUT, "audit.png") });
    console.log("audit.png");
  }

  // 7) map close-up with search open
  await page.click("#msinput");
  await page.keyboard.type("spring");
  await sleep(900);
  const mp = await page.$("#mappanel");
  await mp.screenshot({ path: path.join(OUT, "map.png") });
  console.log("map.png");

  await browser.close();
  console.log("done ->", OUT);
})().catch(e => { console.error(e); process.exit(1); });