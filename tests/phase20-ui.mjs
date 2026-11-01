// Phase 20: Oberflächentest „Personen im Dokument“ (alle Stellen einer Person schwärzen/auslassen).
// Tastatur in Microsoft Edge (headless) über das Chrome DevTools Protocol.
// Keine zusätzlichen Pakete nötig (Node 22+: fetch und WebSocket eingebaut).
//
// Voraussetzungen:
//   - `npm run dev` läuft (http://localhost:1420, Sidecar wird mitgestartet)
//   - Microsoft Edge installiert (anderer Pfad: EDGE_PATH)
// Aufruf:
//   node tests/phase20-ui.mjs <mehrseitig.pdf> <ausgabeordner>
// Edge läuft mit einem temporären Profil im Ausgabeordner; das normale
// Edge-Profil wird nicht verwendet. Bildschirmfotos landen ebenfalls dort.
//
// Phase 5 - installierte App testen: die App mit der Umgebungsvariable
//   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9334
// starten und dann mit CDP_PORT=9334 aufrufen. Der Test hängt sich an das
// App-Fenster (WebView2) an, statt Edge zu starten.

import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const EDGE = process.env.EDGE_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const APP = process.env.APP_URL || "http://localhost:1420";
const [pdfArg, outArg] = process.argv.slice(2);
if (!pdfArg || !outArg) {
  console.error("Aufruf: node tests/phase20-ui.mjs <mehrseitig.pdf> <ausgabeordner>");
  process.exit(2);
}
const pdfPath = resolve(pdfArg);
const outDir = resolve(outArg);
mkdirSync(outDir, { recursive: true });

const ATTACH = !!process.env.CDP_PORT;
const PORT = Number(process.env.CDP_PORT || 9333);
const edge = ATTACH ? null : spawn(
  EDGE,
  [
    "--headless=new",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${join(outDir, "edge-profil")}`,
    "--window-size=1440,900",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "about:blank",
  ],
  { stdio: "ignore" },
);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const consoleErrors = [];

function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "OK  " : "FEHLER"} ${name}${detail ? ` – ${detail}` : ""}`);
}

async function connect() {
  for (let i = 0; i < 50; i++) {
    try {
      if (ATTACH) {
        const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
        const page = targets.find((t) => t.type === "page");
        if (page) return page.webSocketDebuggerUrl;
        throw new Error("noch kein App-Fenster");
      }
      const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
      return target.webSocketDebuggerUrl;
    } catch {
      await sleep(200);
    }
  }
  throw new Error(ATTACH ? "App-Fenster nicht erreichbar" : "Edge nicht erreichbar");
}

const ws = new WebSocket(await connect());
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let seq = 0;
const pending = new Map();
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg);
    pending.delete(msg.id);
  } else if (msg.method === "Runtime.exceptionThrown") {
    consoleErrors.push(msg.params.exceptionDetails?.exception?.description || msg.params.exceptionDetails?.text);
  } else if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
    consoleErrors.push(msg.params.args.map((a) => a.value ?? a.description).join(" "));
  }
});

function send(method, params = {}) {
  return new Promise((res, rej) => {
    const id = ++seq;
    pending.set(id, (msg) => (msg.error ? rej(new Error(`${method}: ${msg.error.message}`)) : res(msg.result)));
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}

async function waitFor(expression, label, timeout = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try {
      const value = await evaluate(expression);
      if (value) return value;
    } catch {
      // Seite lädt noch
    }
    await sleep(150);
  }
  throw new Error(`Zeitüberschreitung: ${label}`);
}

async function screenshot(name) {
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(outDir, `${name}.png`), Buffer.from(data, "base64"));
}

const js = (s) => JSON.stringify(s);
const clickButton = (text) =>
  evaluate(`(() => { const b = [...document.querySelectorAll("button, label")].find(e => e.textContent.includes(${js(text)}) && !e.disabled); if (!b) return false; b.click(); return true; })()`);
const bodyText = () => evaluate("document.body.innerText");
const allCount = () =>
  evaluate(`(() => { const c = [...document.querySelectorAll(".filter-chip")].find(e => e.textContent.startsWith("Alle")); return c ? Number(c.querySelector(".filter-count").textContent) : -1; })()`);

async function key(keyName, { ctrl = false } = {}) {
  const code = keyName.length === 1 ? `Key${keyName.toUpperCase()}` : keyName;
  const vk = keyName.length === 1 ? keyName.toUpperCase().charCodeAt(0) : 0;
  const base = { key: keyName, code, windowsVirtualKeyCode: vk, modifiers: ctrl ? 2 : 0 };
  await send("Input.dispatchKeyEvent", { type: "keyDown", ...base, text: ctrl ? undefined : keyName });
  await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
  await sleep(150);
}

const personRow = (name) =>
  `[...document.querySelectorAll(".person-row")].find(r => r.querySelector(".person-title").textContent.includes(${js(name)}))`;
const chipCount = (label) =>
  evaluate(`(() => { const c = [...document.querySelectorAll(".filter-chip")].find(e => e.textContent.startsWith(${js(label)})); return c ? Number(c.querySelector(".filter-count").textContent) : -1; })()`);

try {
  await send("Page.enable");
  await send("Runtime.enable");
  await send("DOM.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  if (!ATTACH) await send("Page.navigate", { url: APP });
  await waitFor(`document.body.innerText.includes("PDF sicher schwärzen")`, "Startbildschirm", 120000);
  await clickButton("Ohne KI");
  const { root } = await send("DOM.getDocument");
  const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector: ".dropzone input[type=file]" });
  await send("DOM.setFileInputFiles", { nodeId, files: [pdfPath] });
  await waitFor(`!!document.querySelector(".page-surface .page-image")`, "Arbeitsbereich");
  await waitFor(`!!document.querySelector("[data-testid=persons-box]")`, "Kasten „Personen im Dokument“", 15000);
  check("Kasten „Personen im Dokument“ sichtbar", true);

  const meta = await evaluate(`(() => { const r = ${personRow("Hansen")}; return r ? r.querySelector(".person-meta").textContent : ""; })()`);
  const stellen = Number((meta.match(/(\d+) Stellen/) || [])[1] || 0);
  check("Hansen als eine Person mit mehreren Stellen (verschiedene Schreibweisen, mehrere Seiten)", stellen === 3, meta);  // „Frau Hansen“ S. 1, Aufzählung S. 2, „gez. Hansen“ S. 3
  await screenshot("01-personen");

  const rejectedBefore = await chipCount("Verworfen");
  await evaluate(`[...${personRow("Hansen")}.querySelectorAll("button")].find(b => b.textContent.trim() === "Auslassen").click()`);
  await waitFor(`${personRow("Hansen")}.classList.contains("is-skipped")`, "Hansen ausgelassen");
  check("„Auslassen“ verwirft alle Stellen der Person", (await chipCount("Verworfen")) === rejectedBefore + stellen,
    `${await chipCount("Verworfen")} verworfen`);
  check("Andere Personen bleiben unverändert",
    await evaluate(`!${personRow("Möller")}.classList.contains("is-skipped") && ${personRow("Möller")}.querySelector(".person-meta").textContent.includes("offen")`));
  await screenshot("02-ausgelassen");

  await key("z", { ctrl: true });
  await waitFor(`!${personRow("Hansen")}.classList.contains("is-skipped")`, "Rückgängig");
  check("Rückgängig (Strg+Z) nimmt das Auslassen in einem Schritt zurück", (await chipCount("Verworfen")) === rejectedBefore);

  const confirmedBefore = await chipCount("Bestätigt");
  await evaluate(`[...${personRow("Hansen")}.querySelectorAll("button")].find(b => b.textContent.trim() === "Schwärzen").click()`);
  await waitFor(`${personRow("Hansen")}.querySelector(".person-meta").textContent.includes("wird geschwärzt")`, "Hansen bestätigt");
  check("„Schwärzen“ bestätigt alle Stellen der Person", (await chipCount("Bestätigt")) === confirmedBefore + stellen);

  const link = await evaluate(`[...document.querySelectorAll(".bulk-row")].map(e => e.textContent).find(t => t.includes("Stellen von")) || ""`);
  check("Anzeigename ohne Satzzeichen", !(await evaluate(`[...document.querySelectorAll(".person-title")].some(e => /[.,]$/.test(e.textContent) && !/(Dr|Prof)\.$/.test(e.textContent))`)));
  check("Fundstelle zeigt „Alle … Stellen von …: bestätigen · auslassen“", link.includes("bestätigen") && link.includes("auslassen"), link);
  const scroll = await evaluate(`(() => { const s = document.querySelector(".mark-scroll"); return s.scrollWidth <= s.clientWidth + 1; })()`);
  check("Liste ohne waagerechten Rollbalken", scroll);
  check("Keine Fehler in der Browser-Konsole", consoleErrors.length === 0, consoleErrors.join(" | "));
} catch (e) {
  check("Ablauf vollständig", false, e.message);
  try { await screenshot("fehler"); } catch {}
} finally {
  const ok = results.filter((r) => r.ok).length;
  console.log(`\n${ok} von ${results.length} Prüfungen bestanden`);
  writeFileSync(join(outDir, "ergebnis.json"), JSON.stringify(results, null, 2));
  ws.close();
  edge?.kill();
  process.exit(ok === results.length ? 0 : 1);
}
