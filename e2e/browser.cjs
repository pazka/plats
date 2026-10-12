/* End-to-end browser test (Chromium via Playwright). Driven by e2e/run.sh.
 * Env: BASE, ADMIN_PASSWORD, TOTP_SECRET, OUT, PLAYWRIGHT_MODULE (optional). */
"use strict";
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const BASE = process.env.BASE || "http://127.0.0.1:18200";
const OUT = process.env.OUT || "e2e/out";
const results = [];
const problems = [];

function check(cond, label) {
  results.push({ ok: !!cond, label });
  console.log((cond ? "  ok   " : "  FAIL ") + label);
}
async function step(name, fn) {
  console.log(`\n# ${name}`);
  try { await fn(); } catch (e) { check(false, `${name}: ${String(e.message).split("\n")[0]}`); }
}

function totp(secret, at = Date.now()) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret.replace(/=+$/, "")) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const h = crypto.createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1] & 15;
  return String(((h.readUInt32BE(o) & 0x7fffffff) % 1000000)).padStart(6, "0");
}

function watch(page, label) {
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const t = m.text();
    if (/Failed to load resource: the server responded with a status of (4\d\d)/.test(t)) return; // expected API refusals
    problems.push(`[${label}] console error: ${t}`);
  });
  page.on("pageerror", (e) => problems.push(`[${label}] page error: ${e.message}`));
  page.on("request", (r) => {
    const u = r.url();
    if (!u.startsWith(BASE) && !u.startsWith("data:") && !u.startsWith("blob:")) problems.push(`[${label}] external request: ${u}`);
  });
  page.on("dialog", (d) => d.accept());
}

async function newPage(browser, label, opts = {}) {
  const ctx = await browser.newContext({ baseURL: BASE, locale: "fr-FR", ignoreHTTPSErrors: !!process.env.E2E_INSECURE, ...opts });
  const page = await ctx.newPage();
  watch(page, label);
  return page;
}

async function answer(page, key) {
  const before = await page.textContent("#dish-title");
  await page.waitForTimeout(1100); // > think time, < rate limit (3 per 3 s)
  if (key.length === 1) await page.keyboard.press(key);
  else await page.click(key);
  await page.waitForFunction(
    (t) => document.querySelector("#dish-title").textContent !== t || !document.querySelector("#end").hidden,
    before, { timeout: 8000 });
}
const count = async (page) => parseInt(await page.textContent("#count"), 10);
const shot = (page, name, full = false) => page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: full });
const flash = (page) => page.textContent(".flash, .alert").catch(() => "");

(async () => {
  const browser = await chromium.launch({ args: (process.env.E2E_CHROMIUM_ARGS || "").split(";;").filter(Boolean) });

  // ---------------------------------------------------------------- player, desktop
  const play = await newPage(browser, "player", { viewport: { width: 1280, height: 800 } });
  await step("home page + headers", async () => {
    const resp = await play.goto("/");
    const h = resp.headers();
    check(h["content-security-policy"] && h["content-security-policy"].includes("script-src 'self'"), "CSP header");
    check(h["x-frame-options"] === "DENY" && h["x-content-type-options"] === "nosniff", "frame/nosniff headers");
    check((await play.locator(".cats button.cat").count()) === 9, "9 category buttons");
    check((await play.textContent("#dish-title")).trim().length > 0, "a dish is shown");
    const cookies = await play.context().cookies();
    const v = cookies.find((c) => c.name === "plats_v");
    check(v && v.httpOnly && v.sameSite === "Lax", "visitor cookie HttpOnly + SameSite=Lax");
    check((await count(play)) === 0, "count starts at 0");
  });

  await step("first answer: result, meter, colour drift", async () => {
    const hue0 = await play.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--hue").trim());
    await answer(play, "button.cat-tarte");
    const res = await play.textContent("#result");
    check(/\d+ %/.test(res) && res.includes("ont répondu comme toi"), "« X % ont répondu comme toi » shown");
    check((await count(play)) === 1, "count = 1");
    const width = await play.$eval("#meter-fill", (e) => e.style.width);
    check(width && width !== "0%", `progress meter filled (${width})`);
    const hue1 = await play.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--hue").trim());
    check(hue0 !== hue1, `theme hue drifted (${hue0} → ${hue1})`);
  });

  await step("skip (key 0) is not a vote", async () => {
    await answer(play, "0");
    check((await count(play)) === 1, "count still 1 after skip");
    check((await play.textContent("#result")).includes("Pas de souci"), "skip acknowledged");
  });

  await step("milestone at 5, persistence across reload", async () => {
    for (let i = 0; i < 4; i++) await answer(play, String((i % 9) + 1));
    check((await count(play)) === 5, "count = 5 (keyboard answers)");
    check(await play.isVisible("#milestone"), "milestone overlay visible");
    check((await play.textContent("#milestone-title")).includes("Bien joué"), "« Bien joué ! » at 5");
    await shot(play, "01-milestone-5");
    await play.reload();
    check((await count(play)) === 5, "count persists after reload");
  });

  await step("SUPER ! at 25, GÉNIAL + badge at 30", async () => {
    while ((await count(play)) < 25) await answer(play, String(((await count(play)) % 9) + 1));
    check((await play.textContent("#milestone-title")).includes("SUPER"), "SUPER ! at 25");
    await shot(play, "02-super-25");
    while ((await count(play)) < 30) await answer(play, String(((await count(play)) % 9) + 1));
    check((await play.textContent("#milestone-title")).includes("GÉNIAL"), "GÉNIAL at 30");
    check((await play.textContent("#badge")).trim().length > 0, `badge shown (${(await play.textContent("#badge")).trim()})`);
    check((await play.getAttribute("html", "data-palette")) === "neon", "palette upgraded to néon");
    await shot(play, "03-genial-30");
  });

  await step("dish page from the result link", async () => {
    await play.click("#result a.more");
    await play.waitForURL(/\/plat\//);
    check((await play.textContent(".dist")).includes("votre réponse"), "own answer highlighted");
    const widths = await play.$$eval(".dist .bar", (els) => els.map((e) => e.style.width));
    check(widths.length >= 9 && widths.some((w) => w && w !== "0%"), "distribution bars drawn");
    await shot(play, "04-dish-page", true);
  });

  // ---------------------------------------------------------------- reduced motion
  await step("prefers-reduced-motion", async () => {
    const p = await newPage(browser, "reduced", { reducedMotion: "reduce" });
    await p.goto("/");
    for (let i = 0; i < 5; i++) await answer(p, String(i + 1));
    check(await p.isVisible("#milestone"), "milestone still shown as text");
    check(await p.evaluate(() => document.body.classList.contains("pulse") || document.body.classList.contains("pulse-big")),
      "colour pulse instead of motion");
    await p.context().close();
  });

  // ---------------------------------------------------------------- no JavaScript
  await step("no-JS fallback", async () => {
    const p = await newPage(browser, "nojs", { javaScriptEnabled: false });
    await p.goto("/");
    await p.waitForTimeout(700);
    await p.click("button.cat-soupe");
    await p.waitForURL(/apres=/);
    check((await p.textContent("#result")).includes("ont répondu comme toi"), "result page after a form POST");
    await p.waitForTimeout(700);
    await p.click("button.skip");
    await p.waitForURL((u) => u.pathname === "/" && !u.search.includes("apres"));
    check(true, "skip via form POST");
    await p.goto("/proposer");
    check((await p.content()).includes("JavaScript est nécessaire"), "proposal form explains JS is needed");
    await p.context().close();
  });

  // ---------------------------------------------------------------- mobile + dark
  await step("mobile layout (390×844) has no horizontal scroll", async () => {
    const p = await newPage(browser, "mobile", { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    for (const url of ["/", "/proposer", "/plats", "/stats", "/a-propos", "/plat/cassoulet"]) {
      await p.goto(url);
      const over = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      check(over <= 0, `${url}: no overflow (${over}px)`);
    }
    await p.goto("/");
    await shot(p, "05-mobile-home", true);
    await p.context().close();
  });

  await step("dark mode", async () => {
    const p = await newPage(browser, "dark", { colorScheme: "dark", viewport: { width: 1000, height: 900 } });
    await p.goto("/");
    const bg = await p.evaluate(() => getComputedStyle(document.querySelector(".dish-card")).backgroundColor);
    check(bg !== "rgb(255, 255, 255)", `dark card background (${bg})`);
    await shot(p, "06-dark-home");
    await p.goto("/stats");
    await shot(p, "07-dark-stats", true);
    await p.context().close();
  });

  // ---------------------------------------------------------------- proposer
  const visitor = await newPage(browser, "proposer", { viewport: { width: 1100, height: 900 } });
  async function propose(name, desc, region = "") {
    await visitor.goto("/proposer");
    await visitor.fill("#nom", name);
    await visitor.fill("#description", desc);
    if (region) await visitor.fill("#region", region);
    return submitAndWait(visitor, "#propose-submit");
  }
  async function submitAndWait(page, selector) {
    // The form is submitted by JS after the proof-of-work: wait for the *next* document.
    await page.evaluate(() => { window.__before = true; });
    const t0 = Date.now();
    await page.click(selector);
    await page.waitForFunction(() => !window.__before && document.readyState !== "loading", null, { timeout: 30000 });
    return Date.now() - t0;
  }
  const DESC = "Plat servi chaud dans une assiette creuse, avec une garniture généreuse et du pain.";

  await step("proposer: live look-alike suggestions", async () => {
    await visitor.goto("/proposer");
    await visitor.fill("#nom", "Quiche lorainne");
    await visitor.waitForFunction(() => document.querySelector("#similar-live").textContent.includes("Quiche lorraine"),
      null, { timeout: 5000 });
    check(true, "suggestion « Quiche lorraine » appears while typing");
    await visitor.fill("#description", "abc");
    check((await visitor.textContent("#desc-counter")).includes("au minimum"), "description counter");
  });

  await step("proposer: validation, look-alike → « Non, c'est un autre plat »", async () => {
    await propose("Quiche lorainne", "trop court");
    check((await visitor.textContent(".alert")).includes("La description doit faire"), "server-side length validation");
    const ms = await propose("Quiche lorainne", "Version sans lardons, avec des poireaux fondus et du chèvre, servie tiède.");
    check((await visitor.textContent("body")).includes("C'est peut-être l'un de ceux-là"), `look-alike page (PoW+submit ${ms} ms)`);
    await submitAndWait(visitor, "button[value=different]");
    check((await visitor.textContent("h1")).includes("Merci"), "Merci page");
    check(true, "« Non, c'est un autre plat » creates the dish");
  });

  await step("proposer: look-alike → « Oui, c'est celui-là »", async () => {
    await propose("Cassoulets", DESC);
    await submitAndWait(visitor, "button[value^='same:']");
    check((await visitor.textContent("main")).includes("déjà dans la liste"), "duplicate acknowledged, nothing created");
  });

  await step("proposer: banned word and normal submission", async () => {
    await propose("Plat de c0nnard", DESC);
    check((await visitor.textContent("h1")).includes("Merci"), "flagged submission still says Merci");
    await propose("Garbure béarnaise", "Soupe épaisse au chou, aux haricots et au confit, servie très chaude.", "Béarn");
    check((await visitor.textContent("h1")).includes("Merci"), "normal submission accepted");
  });

  // ---------------------------------------------------------------- browse, dish, stats
  await step("browse / search / sort / pagination", async () => {
    await visitor.goto("/plats?q=GRATIN+DAUPHINOIS");
    check((await visitor.textContent(".dish-list")).includes("Gratin dauphinois"), "accent/case-insensitive search");
    await visitor.goto("/plats?q=garbure");
    check((await visitor.textContent(".dish-list")).includes("Garbure"), "user submission searchable");
    await visitor.goto("/plats?q=c0nnard");
    check(!(await visitor.textContent(".dish-list")).includes("c0nnard"), "flagged submission not public");
    for (const tri of ["votes", "consensus", "dispute", "recent", "az"]) {
      await visitor.goto(`/plats?tri=${tri}`);
      check((await visitor.locator(".dish-list li").count()) > 0, `sort « ${tri} » lists dishes`);
    }
    await visitor.goto("/plats");
    await visitor.click("text=Suivant →");
    check(visitor.url().includes("page=2"), "pagination");
  });

  await step("dish page, stats, about, 404", async () => {
    await visitor.goto("/plat/cassoulet");
    check((await visitor.locator(".dist li").count()) >= 9, "distribution rows on /plat/cassoulet");
    await visitor.goto("/stats");
    const tiles = await visitor.textContent(".tiles");
    check(/\d/.test(tiles) && tiles.includes("réponses"), "stats tiles");
    check((await visitor.$$eval(".per-day .bar", (els) => els.filter((e) => e.style.height).length)) > 0, "answers-per-day chart drawn");
    check((await visitor.locator(".coverage li").count()) === 3, "coverage ≥1/≥5/≥20");
    await shot(visitor, "08-stats", true);
    await visitor.goto("/a-propos");
    check((await visitor.textContent("main")).includes("Ce qui est enregistré"), "about page");
    const r = await visitor.goto("/plat/nexiste-pas");
    check(r.status() === 404 && (await visitor.textContent("main")).includes("n'existe pas"), "friendly 404");
  });

  // ---------------------------------------------------------------- admin
  const admin = await newPage(browser, "admin", { viewport: { width: 1400, height: 900 }, acceptDownloads: true });
  await step("admin login", async () => {
    await admin.goto("/admin");
    check(admin.url().endsWith("/admin/login"), "/admin redirects to login");
    await admin.fill("#password", "wrong-password");
    await admin.fill("#totp", "123456");
    await admin.click("button[type=submit]");
    check((await admin.textContent(".alert")).includes("Identifiants invalides"), "wrong password refused");
    await admin.fill("#password", process.env.ADMIN_PASSWORD);
    await admin.fill("#totp", totp(process.env.TOTP_SECRET));
    await admin.click("button[type=submit]");
    await admin.waitForURL(/\/admin$/);
    check((await admin.textContent("h1")).includes("Tableau de bord"), "logged in with password + TOTP");
    const cookies = await admin.context().cookies();
    const s = cookies.find((c) => c.name === "plats_admin");
    check(s && s.httpOnly && s.sameSite === "Strict" && s.path === "/admin", "admin cookie HttpOnly, Strict, Path=/admin");
    await shot(admin, "10-admin-dashboard", true);
  });

  await step("admin: CSRF enforced", async () => {
    const status = await admin.evaluate(async () => (await fetch("/admin/reglages", {
      method: "POST", body: new URLSearchParams({ require_approval: "1" }), redirect: "manual" })).status);
    check(status === 403, `POST without CSRF token → 403 (${status})`);
  });

  await step("admin: submissions queue (flagged → reject)", async () => {
    await admin.goto("/admin/propositions");
    const card = admin.locator("article.submission", { hasText: "c0nnard" });
    check((await card.count()) === 1, "flagged submission in the queue");
    check((await card.textContent()).includes("connard"), "flag reason shown");
    await card.locator("button[value=reject]").click();
    check((await flash(admin)).includes("rejeté"), "rejected");
  });

  await step("admin: edit, hide/activate, create", async () => {
    await admin.goto("/admin/plats?source=user");
    check((await admin.textContent("table")).includes("Garbure"), "filter by source=user");
    await admin.click("text=Garbure béarnaise");
    await admin.fill("input[name=region]", "Béarn (Pyrénées)");
    await admin.click("form.card button[type=submit]");
    check((await flash(admin)).includes("Enregistré"), "dish edited");
    await admin.goto("/admin/plats?q=garbure");
    await admin.check("input[name=ids]");
    await admin.selectOption("#bulk-action", "hide");
    await admin.click("#bulk button[type=submit]");
    check((await flash(admin)).includes("mis à jour"), "bulk hide");
    check((await visitor.goto("/plat/garbure-bearnaise")).status() === 404, "hidden dish is gone from the public site");
    await admin.goto("/admin/plats?q=garbure");
    await admin.check("input[name=ids]");
    await admin.selectOption("#bulk-action", "activate");
    await admin.click("#bulk button[type=submit]");
    check((await visitor.goto("/plat/garbure-bearnaise")).status() === 200, "re-activated");
    await admin.goto("/admin/plats");
    await admin.fill("form[action='/admin/plats/nouveau'] input[name=title]", "Tourte de l'admin");
    await admin.fill("form[action='/admin/plats/nouveau'] textarea", "Tourte ajoutée à la main depuis l'administration.");
    await admin.click("form[action='/admin/plats/nouveau'] button");
    check((await flash(admin)).includes("Plat créé"), "manual dish created");
  });

  await step("admin: merge duplicate", async () => {
    await admin.goto("/admin/plats?q=quiche+lorainne");
    await admin.click("table a:has-text('Quiche lorainne')");
    await admin.fill("form.inline-form input[name=target]", "quiche-lorraine");
    await admin.click("form.inline-form button");
    check((await flash(admin)).includes("fusionné"), "merged into « Quiche lorraine »");
    await visitor.goto("/plat/quiche-lorainne");
    check(visitor.url().endsWith("/plat/quiche-lorraine"), "old URL redirects to the merged dish");
  });

  await step("admin: require_approval → pending → approve", async () => {
    await admin.goto("/admin/reglages");
    await admin.check("input[name=require_approval]");
    await admin.click("form.card button[type=submit]");
    check((await flash(admin)).includes("enregistrés"), "settings saved");
    await propose("Flammekueche", "Pâte très fine garnie de crème, d'oignons et de lardons, cuite au four à bois, en part.", "Alsace");
    await visitor.goto("/plats?q=flammekueche");
    check(!(await visitor.textContent(".dish-list")).includes("Flammekueche"), "pending dish not public");
    await admin.goto("/admin/propositions");
    const card = admin.locator("article.submission", { hasText: "Flammekueche" });
    check((await card.textContent()).includes("pending"), "pending in the queue");
    await card.locator("button[value=approve]").click();
    await visitor.goto("/plats?q=flammekueche");
    check((await visitor.textContent(".dish-list")).includes("Flammekueche"), "approved dish is public");
    await admin.goto("/admin/reglages");
    await admin.uncheck("input[name=require_approval]");
    await admin.click("form.card button[type=submit]");
  });

  await step("admin: answers browse / filter / delete", async () => {
    await admin.goto("/admin/votes");
    const total0 = parseInt((await admin.textContent("h1")).replace(/\D/g, ""), 10);
    check(total0 >= 35, `answers listed (${total0})`);
    await admin.goto("/admin/votes?categorie=tarte");
    check((await admin.locator("tbody tr").count()) >= 1, "filter by category");
    await admin.goto("/admin/votes");
    await admin.check("tbody input[name=ids] >> nth=0");
    await admin.click("#sel button");
    check((await flash(admin)).includes("1 réponse(s) supprimée(s)"), "delete one selected answer");
    await admin.goto("/admin/votes");
    await admin.click("tbody tr >> nth=0 >> td >> nth=4 >> a");
    check(admin.url().includes("visiteur="), "filter by visitor via link");
    await admin.click("form.card:has(select[name=mode]) button");
    check((await flash(admin)).includes("supprimée"), "bulk delete by visitor");
  });

  await step("admin: categories", async () => {
    await admin.goto("/admin/categories");
    check((await admin.locator("tbody tr").count()) === 9, "9 seeded categories");
    const withVotes = admin.locator("tbody tr", { hasText: "tarte" });
    check((await withVotes.locator("button[value=delete]").count()) === 0, "no delete button on a category with votes");
    const create = admin.locator("form:has(input[value=create])");
    await create.locator("input[name=name]").fill("Galette");
    await create.locator("input[name=color]").fill("#123456");
    await create.locator("input[name=emoji]").fill("🫓");
    await create.locator("button").click();
    check((await flash(admin)).includes("mises à jour"), "category created");
    await visitor.goto("/");
    check((await visitor.locator("button.cat-galette").count()) === 1, "new category playable");
    const row = admin.locator("tbody tr", { hasText: "galette" });
    await row.locator("input[name=active]").uncheck();
    await row.locator("button[value=update]").click();
    await visitor.goto("/");
    check((await visitor.locator("button.cat-galette").count()) === 0, "deactivated category hidden from the game");
    await admin.locator("tbody tr", { hasText: "galette" }).locator("button[value=delete]").click();
    check(!(await admin.textContent("tbody")).includes("galette"), "unused category deleted");
  });

  await step("admin: banned words", async () => {
    await admin.goto("/admin/mots");
    await admin.fill("input[name=word]", "zorglub");
    await admin.click("button[value=add]");
    check((await flash(admin)).includes("ajouté"), "word added");
    await admin.fill("textarea[name=text]", "un z0rglubbb ici");
    await admin.click("button[value=test]");
    check((await admin.textContent("main")).includes("Bloqué"), "test box detects leetspeak/repeats");
    await admin.fill("textarea[name=bulk]", "machinchose\ntrucmuche|substring");
    await admin.click("button[value=import]");
    check((await flash(admin)).includes("2 mot(s) importé(s)"), "bulk import");
    await admin.click("button[value=rescan]");
    check((await flash(admin)).includes("Re-scan terminé"), "rescan");
    await admin.check("tr:has-text('zorglub') input[name=ids]");
    await admin.click("button[value=remove]");
    check((await flash(admin)).includes("retiré"), "word removed");
  });

  await step("admin: export .xlsx", async () => {
    await admin.goto("/admin");
    const [dl] = await Promise.all([admin.waitForEvent("download"), admin.click("nav a[href='/admin/export.xlsx']")]);
    await dl.saveAs(path.join(OUT, "export.xlsx"));
    const size = fs.statSync(path.join(OUT, "export.xlsx")).size;
    check(dl.suggestedFilename().startsWith("plats-export-") && size > 5000, `export downloaded (${size} bytes)`);
  });

  await step("admin: Wikidata import button", async () => {
    await admin.goto("/admin/plats");
    await admin.uncheck("input[name=enrich]");
    await admin.click("form[action='/admin/import'] button");
    check((await flash(admin)).includes("Import Wikidata lancé"), "import started in background");
    await admin.waitForTimeout(4000);
    await admin.goto("/admin");
    const st = await admin.textContent("main");
    check(/Import en cours|Dernier import/.test(st), "import status reported on the dashboard (running, done or failed)");
    check(st.includes("Dernier import en échec") ? (await admin.locator(".alert").count()) > 0 : true,
      "an offline import failure is shown as an error, not a crash");
  });

  await step("admin: audit log, logout", async () => {
    await admin.goto("/admin/journal");
    const log = await admin.textContent("table");
    for (const a of ["login", "submission_reject", "dish_edit", "dish_merge", "settings_update", "votes_delete",
      "category_create", "banned_word_add", "export_xlsx", "import_wikidata"]) check(log.includes(a), `journal has ${a}`);
    await admin.click("button:has-text('Déconnexion')");
    await admin.goto("/admin");
    check(admin.url().endsWith("/admin/login"), "logged out");
  });

  await step("admin: lockout after repeated failures", async () => {
    const p = await newPage(browser, "attacker");
    for (let i = 0; i < 6; i++) {
      await p.goto("/admin/login");
      await p.fill("#password", "guess-" + i);
      await p.fill("#totp", "000000");
      await p.click("button[type=submit]");
    }
    check((await p.textContent(".alert")).includes("Trop d"), "locked out (« Trop d'échecs »)");
    await p.context().close();
  });

  await browser.close();

  console.log("\n# browser problems (console errors, CSP violations, external requests)");
  check(problems.length === 0, problems.length ? problems.slice(0, 10).join("\n         ") : "none");
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} browser checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
