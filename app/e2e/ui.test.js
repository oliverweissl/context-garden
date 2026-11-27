// UI regression suite: drives the real app UI (built from src/, backed by src/mock.js in place of
// Tauri) in headless Chrome and checks that the main screens and flows keep working.
// Run with `npm run test:ui`. Needs Chrome/Chromium (CHROME_PATH, or a standard location);
// skips with a notice when there is none.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import puppeteer from "puppeteer-core";

const CANDIDATES = [
  process.env.CHROME_PATH,
  "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
].filter(Boolean);
const chrome = CANDIDATES.find((p) => fs.existsSync(p));

let server;
let browser;
let url;
let page;
const errors = [];
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (sel) => page.$eval(sel, (e) => e.innerText);

before(async () => {
  if (!chrome) return;
  server = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), server: { port: 0 }, logLevel: "error" });
  await server.listen();
  url = server.resolvedUrls.local[0];
  browser = await puppeteer.launch({ executablePath: chrome, headless: true, args: ["--no-sandbox"] });
});

after(async () => {
  await browser?.close();
  await server?.close();
});

/** A fresh page on `hash`; JS errors are collected and fail the test that caused them. */
async function open(hash = "") {
  await page?.close();
  page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 900 });
  errors.length = 0;
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(url + hash);
  await page.waitForSelector("#app *");
  await wait(400);
}

/** Click the first element of `sel` whose text contains `label`. */
async function clickText(sel, label) {
  const handles = await page.$$(sel);
  for (const h of handles) {
    if ((await h.evaluate((e) => e.innerText)).includes(label)) return h.click();
  }
  throw new Error(`no ${sel} containing "${label}"`);
}

const ui = (name, fn) => test(name, { skip: chrome ? false : "no Chrome found (set CHROME_PATH)" }, async () => {
  await fn();
  assert.deepEqual(errors, [], `page errors: ${errors.join("; ")}`);
});

ui("home lists the gardens with their state and a way to start one", async () => {
  await open();
  const t = await text("#app");
  assert.match(t, /payments-platform/);
  assert.match(t, /6 repos/);
  assert.match(t, /1 failed/);
  assert.match(t, /solo-tool/);
  const links = await page.$$eval("#app a", (as) => as.map((a) => a.getAttribute("href")));
  assert.ok(links.includes("#/garden/payments-platform") && links.includes("#/new"), links.join());
});

ui("a garden page shows its statistics and the terminal toolbar", async () => {
  await open("#/garden/payments-platform");
  await page.waitForSelector(".toolbar");
  const t = await text("#app");
  for (const part of ["REPOSITORIES", "STATISTICS", "TOKENS BY ACCOUNT"]) assert.match(t.toUpperCase(), new RegExp(part));
  assert.match(await text(".toolbar"), /Orchestrate/);
  assert.ok(await page.$(".resume-bar"), "saved terminals offer a resume bar");
});

ui("the logo leads to the main menu from a garden, and again when already there (#65)", async () => {
  await open("#/garden/payments-platform");
  await page.waitForSelector(".toolbar");
  await page.click(".brand");
  await wait(300);
  assert.equal(new URL(page.url()).hash, "#/");
  assert.match(await text("#app"), /YOUR GARDENS/i);
  await page.click(".brand");
  await wait(200);
  assert.match(await text("#app"), /YOUR GARDENS/i);
});

ui("leaving a page closes its popups", async () => {
  await open("#/garden/payments-platform");
  await page.waitForSelector(".toolbar");
  await page.click("#settings");
  await page.waitForSelector("dialog.modal[open]");
  await page.evaluate(() => { location.hash = "#/"; });
  await wait(300);
  assert.equal(await page.$("dialog.modal[open]"), null);
});

ui("settings open and close", async () => {
  await open();
  await page.click("#settings");
  await page.waitForSelector("dialog.modal[open]");
  await page.click("dialog.modal[open] .modal-head .icon");
  await wait(200);
  assert.equal(await page.$("dialog.modal[open]"), null);
});

ui("the Network settings tab shows the built-in agent list and adds a list (#81)", async () => {
  await open();
  await page.click("#settings");
  await page.waitForSelector("dialog.modal[open]");
  await clickText(".settings-tabs button", "Network");
  await page.waitForSelector(".network .net-list");
  const body = await text(".settings-body");
  assert.match(body, /Network security layer/);
  assert.match(body, /built-in/);
  assert.equal(await page.$eval(".network input[type=checkbox]", (b) => b.checked), false, "off by default");
  // The mock machine has only the Landlock fallback: its hole is stated and it needs an agreement.
  assert.match(await text(".net-mode"), /Landlock fallback[\s\S]*any host on the proxy's port number[\s\S]*Until you agree/);
  await clickText(".net-mode button", "I understand, use the fallback");
  await page.waitForFunction(() => /You agreed/.test(document.querySelector(".net-mode").textContent));
  await clickText(".net-mode button", "Withdraw agreement");
  await page.waitForFunction(() => /Until you agree/.test(document.querySelector(".net-mode").textContent));
  const before = (await page.$$(".network .net-list")).length;
  await clickText(".network button", "+ Deny list");
  await page.waitForFunction((n) => document.querySelectorAll(".network .net-list").length === n + 1, {}, before);
  await page.click("dialog.modal[open] .modal-head .icon");
});

ui("resuming terminals opens agent panes with icon, title and a detected checklist", async () => {
  await open("#/garden/payments-platform");
  await page.waitForSelector(".resume-bar");
  await clickText(".resume-bar button", "Resume all");
  await page.waitForSelector(".pane");
  const panes = await page.$$(".pane");
  assert.ok(panes.length >= 2, `panes: ${panes.length}`);
  const head = await text(".pane-head");
  assert.match(head, /Claude Code/);
  assert.ok(await page.$(".pane-head svg, .pane-head [aria-label]"), "agent icon in the pane head");
  await page.waitForSelector(".checklist-chip:not([hidden])", { timeout: 8000 }); // the mock agent prints steps
});

ui("an uncovered connection asks in the terminal's window with four answers (#81)", async () => {
  await open("#/garden/payments-platform");
  await page.waitForSelector(".resume-bar");
  await clickText(".resume-bar button", "Resume all");
  await page.waitForSelector(".pane");
  // A prompt for a terminal this window does not hold is not shown here.
  await page.evaluate(() => window.cgMockEmit("net-prompt", { id: 1, key: "elsewhere", garden: "payments-platform", terminal: "X", host: "x.example", port: 443, ips: [], timeout_secs: 60 }));
  await wait(300);
  assert.equal(await page.$(".net-prompt"), null);
  await page.evaluate(() => window.cgMockEmit("net-prompt", { id: 2, key: "a", garden: "payments-platform", terminal: "Claude Code", host: "api.github.com", port: 443, ips: ["140.82.112.6"], timeout_secs: 60 }));
  await page.waitForSelector(".net-prompt");
  const body = await text(".net-prompt");
  assert.match(body, /Claude Code/);
  assert.match(body, /api\.github\.com:443/);
  assert.match(body, /Declined in \d+ s/);
  const labels = await page.$$eval(".net-prompt .net-choices button", (bs) => bs.map((b) => b.textContent));
  assert.deepEqual(labels, ["Accept once", "Accept and add to", "Decline once", "Decline and add to"]);
  assert.ok(body.includes("*.github.com"), "domain suggestion");
  await clickText(".net-prompt button", "Accept once");
  await page.waitForFunction(() => !document.querySelector(".net-prompt"));
});

ui("the Orchestrate view lists the open agent terminals", async () => {
  await open("#/garden/payments-platform");
  await page.waitForSelector(".resume-bar");
  await clickText(".resume-bar button", "Resume all");
  await page.waitForSelector(".pane");
  await clickText(".toolbar button", "Orchestrate");
  await page.waitForSelector("dialog.modal[open] .og-node");
  assert.match(await text("dialog.modal[open]"), /Orchestrate agents/);
  assert.ok((await page.$$("dialog.modal[open] .og-node")).length >= 1);
});

ui("outdated skills are flagged in the top bar and listed with an update (#64)", async () => {
  await open();
  const label = await page.$$eval("header.nav button", (bs) => bs.map((b) => b.innerText).find((t) => /Skills outdated/.test(t)));
  assert.match(label, /Skills outdated \(\d+\)/);
  await clickText("header.nav button", "Skills outdated");
  await page.waitForSelector("dialog.modal[open]");
  assert.match(await text("dialog.modal[open]"), /Update/);
});

ui("the plant page (new garden) opens", async () => {
  await open("#/new");
  assert.ok((await text("#app")).length > 50);
});

ui("a garden can start empty: no sources, the button plants it", async () => {
  await open("#/new");
  assert.match(await text("#app .btn.primary"), /Plant empty garden/);
  await page.type("textarea", "~/code/tool");
  assert.match(await text("#app .btn.primary"), /Find repositories/);
  await page.$eval("textarea", (e) => { e.value = ""; e.dispatchEvent(new Event("input")); });
  assert.match(await text("#app .btn.primary"), /Plant empty garden/);
});
