// Minimal Chrome DevTools Protocol client: Node built-ins only (fetch, WebSocket, child_process).
// Same approach as pazka/hosh-hub tools/interact.mjs: no package is installed for the visual harness.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

/** Starts headless Chrome ($CHROME or google-chrome) and returns { page(), close() }. */
export async function launch() {
  const port = 9300 + (process.pid % 500);
  const profile = mkdtempSync(join(tmpdir(), "plats-visual-"));
  const chrome = spawn(process.env.CHROME || "google-chrome", [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--mute-audio", "--no-first-run",
    "--hide-scrollbars", "--force-color-profile=srgb", "--font-render-hinting=none",
    "--disable-features=Translate,MediaRouter", "--disable-background-networking",
    // a desktop visitor has a mouse (hover: hover, pointer: fine): headless Chrome would say "none"
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
    `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "about:blank",
  ], { stdio: "ignore" });
  let url;
  for (let i = 0; i < 100 && !url; i++) {
    try { url = (await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()).webSocketDebuggerUrl; }
    catch { await sleep(100); }
  }
  if (!url) { chrome.kill(); throw new Error("Chrome did not start (set CHROME=/path/to/chrome)"); }
  const version = (await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()).Browser;
  const ws = new WebSocket(url);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  const listeners = new Set();
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) {
      const { res, rej } = pending.get(d.id);
      pending.delete(d.id);
      d.error ? rej(new Error(`${d.error.message}`)) : res(d.result);
    } else for (const l of listeners) l(d);
  };
  const raw = (method, params = {}, sessionId) => new Promise((res, rej) => {
    const id = ++seq;
    pending.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });

  /** A new tab in its own browser context (own cookies = a fresh visitor). */
  async function page() {
    const { browserContextId } = await raw("Target.createBrowserContext", { disposeOnDetach: true });
    const { targetId } = await raw("Target.createTarget", { url: "about:blank", browserContextId });
    const { sessionId } = await raw("Target.attachToTarget", { targetId, flatten: true });
    const send = (method, params) => raw(method, params, sessionId);
    const errors = [];
    listeners.add((d) => {
      if (d.sessionId !== sessionId) return;
      if (d.method === "Runtime.exceptionThrown") {
        errors.push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
      }
    });
    await send("Page.enable");
    await send("Runtime.enable");
    const evaluate = async (expression) => {
      const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) throw new Error(`evaluate failed: ${r.exceptionDetails.text}`);
      return r.result.value;
    };
    const waitFor = async (expression, ms = 10000) => {
      const end = Date.now() + ms;
      while (Date.now() < end) {
        try { if (await evaluate(expression)) return true; } catch { /* navigating */ }
        await sleep(50);
      }
      return false;
    };
    const goto = async (href) => {
      await send("Page.navigate", { url: href });
      await sleep(100);
      if (!(await waitFor("document.readyState === 'complete'"))) throw new Error(`page did not load: ${href}`);
      await evaluate("document.fonts ? document.fonts.ready.then(() => true) : true");
    };
    const close = () => raw("Target.closeTarget", { targetId });
    return { send, evaluate, waitFor, goto, close, errors };
  }

  function close() {
    try { ws.close(); } catch { /* closed */ }
    chrome.kill();
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* busy */ }
  }
  return { page, close, version };
}
