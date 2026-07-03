/* Capture the governance block from the incident that has a pending plan. */
const path = require("path");
const os = require("os");
const puppeteer = require(path.join(
  os.tmpdir(), "nexus-shots", "node_modules", "puppeteer-core"));
const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const OUT = path.resolve(__dirname, "..", "ui", "landing-assets");
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: "new",
    args: ["--window-size=1600,1000", "--hide-scrollbars"],
    defaultViewport: { width: 1600, height: 1200, deviceScaleFactor: 1.5 },
  });
  const page = await browser.newPage();
  await page.goto("http://127.0.0.1:8899",
                  { waitUntil: "networkidle2", timeout: 60000 });
  await page.waitForSelector("#li_user", { visible: true });
  await page.click("#loginbox button.primary");
  await page.waitForSelector("#loginoverlay", { hidden: true, timeout: 20000 });
  await sleep(7000);

  // find the incident whose plan is pending_approval and open its workspace
  const ok = await page.evaluate(() => {
    const pend = (status_?.plans || [])
      .find(p => p.status === "pending_approval");
    if (!pend) return false;
    window.selectIncident(pend.incident_id);
    return true;
  });
  if (!ok) { console.error("no pending plan"); process.exit(1); }
  await sleep(2500);

  const gov = await page.$("#incidents .gov");
  if (!gov) { console.error("no .gov block rendered"); process.exit(1); }
  await gov.screenshot({ path: path.join(OUT, "governance.png") });
  console.log("governance.png");
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });