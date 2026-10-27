// Oberflächentest im echten Browser (Phase 4): bedient die App mit Maus und
// Tastatur in Microsoft Edge (headless) über das Chrome DevTools Protocol.
// Keine zusätzlichen Pakete nötig (Node 22+: fetch und WebSocket eingebaut).
//
// Voraussetzungen:
//   - `npm run dev` läuft (http://localhost:1420, Sidecar wird mitgestartet)
//   - Microsoft Edge installiert (anderer Pfad: EDGE_PATH)
// Aufruf:
//   node tests/ui-smoke.mjs <test.pdf> <ausgabeordner>
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
  console.error("Aufruf: node tests/ui-smoke.mjs <test.pdf> <ausgabeordner>");
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

async function drag(x1, y1, x2, y2) {
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x1, y: y1 });
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: x1, y: y1, button: "left", buttons: 1, clickCount: 1 });
  for (let i = 1; i <= 8; i++) {
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x1 + ((x2 - x1) * i) / 8, y: y1 + ((y2 - y1) * i) / 8, button: "left", buttons: 1 });
  }
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: x2, y: y2, button: "left", buttons: 0, clickCount: 1 });
  await sleep(300);
}

async function clickAt(x, y) {
  await drag(x, y, x, y);
}

/** PDF-Punkte der Seite -> Bildschirmkoordinaten (über Seitenrand und Zoom). */
async function pagePoint(xPt, yPt) {
  const r = await evaluate(`(() => { const s = document.querySelector(".page-surface").getBoundingClientRect(); return {x: s.left, y: s.top, w: s.width}; })()`);
  const scale = r.w / 595;
  return [r.x + xPt * scale, r.y + yPt * scale];
}

try {
  await send("Page.enable");
  await send("Runtime.enable");
  await send("DOM.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  if (!ATTACH) await send("Page.navigate", { url: APP });

  // 1. Start
  await waitFor(`document.body.innerText.includes("PDF sicher schwärzen")`, "Startbildschirm");
  await waitFor(`document.body.innerText.includes("Netzwerkzugriffe gesperrt")`, "Systemzustand");
  // Branding wird nach dem Verbinden geladen; ohne Branding-Ordner gibt es kein Logo
  await waitFor(`!!document.querySelector(".brand-logo")`, "Branding", 10000).catch(() => undefined);
  const text = await bodyText();
  check("Startbildschirm mit drei Modi", ["Ohne KI", "Schnell-KI", "Gründliche KI"].every((t) => text.includes(t)));
  const accent = await evaluate(`getComputedStyle(document.documentElement).getPropertyValue("--accent-l").trim()`);
  if (process.env.UI_BRANDING === "beispiel") {
    check("Branding: Kundenname und Logo", text.includes("Stadtwerke Musterhausen") && (await evaluate(`!!document.querySelector(".brand-logo")`)));
    check("Branding: Akzentfarbe gesetzt", accent === "#0F6B6B", accent);
    check("Branding: Hinweis „bereitgestellt mit“", text.includes("bereitgestellt mit Schwärzsiegel"));
  } else {
    check("Standarddesign ohne Branding", text.includes("Schwärzsiegel") && !(await evaluate(`!!document.querySelector(".brand-logo")`)));
    check("Standard-Akzentfarbe Siegellack-Rot", accent === "#9B2A24", accent);
  }
  check("Keine Google Fonts", !(await evaluate(`[...document.querySelectorAll("link")].some(l => l.href.includes("googleapis"))`)));
  await screenshot("01-start");
  // Phase 6/11: Info-Dialog mit AGPL-Hinweis und verwendeter Software
  await evaluate(`document.querySelector("button[aria-label='Info und Kontakt']").click()`);
  await waitFor(`!!document.querySelector(".modal")`, "Info-Dialog");
  // textContent statt innerText: Überschriften stehen per CSS in Großbuchstaben
  const info = await evaluate(`document.querySelector(".modal").textContent`);
  check("Info-Dialog: AGPL-Hinweis, Quellcode und Lizenzhinweise",
    info.includes("GNU Affero General Public License") && info.includes("Die Lizenz selbst enthält keine Gewährleistung")
      && info.includes("gelten zusätzlich die Bedingungen Ihrer Vereinbarung")
      && info.includes("Quellcode") && info.includes("Verwendete Software und Modelle") && info.includes("PyMuPDF")
      && info.includes("per E-Mail an info@schwaerzsiegel.de"));
  check("Info-Dialog: keine Lizenzdatei mehr", !info.includes("Lizenznummer") && !info.includes("Lizenz einspielen"));
  await screenshot("01b-info");
  await clickButton("Schließen");
  await waitFor(`!document.querySelector(".modal")`, "Info-Dialog geschlossen");

  await clickButton("Ohne KI");
  check("Modus A wählbar", await evaluate(`[...document.querySelectorAll(".mode-card")].find(c => c.textContent.includes("Ohne KI")).classList.contains("is-selected")`));

  // 2. Dokument öffnen
  const { root } = await send("DOM.getDocument");
  const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector: ".dropzone input[type=file]" });
  await send("DOM.setFileInputFiles", { nodeId, files: [pdfPath] });
  await waitFor(`!!document.querySelector(".page-surface .page-image")`, "Arbeitsbereich mit Seitenbild");
  await sleep(800);
  const initial = await allCount();
  check("Arbeitsbereich geöffnet, Vorschläge der Regeln", initial >= 3, `${initial} Fundstellen`);
  check("Arbeitsschritte sichtbar", (await evaluate(`document.querySelectorAll(".steps .step").length`)) === 6);
  const headerHeight = await evaluate(`document.querySelector(".ws .app-header").getBoundingClientRect().height`);
  check("Kopfzeile einzeilig bei 1440 px", headerHeight < 70, `${Math.round(headerHeight)} px`);
  check("Seite passt in die Breite (kein waagerechter Rollbalken)",
    await evaluate(`(() => { const s = document.querySelector(".canvas-scroll"); return s.scrollWidth <= s.clientWidth + 1; })()`));
  await screenshot("02-arbeitsbereich");

  // 3. Rechteck aufziehen (Taste R)
  await key("r");
  check("Werkzeug Rechteck per Taste R", (await evaluate(`document.querySelector(".tool-btn.is-active").textContent`)).includes("Rechteck"));
  let [x1, y1] = await pagePoint(66, 168);
  let [x2, y2] = await pagePoint(200, 186);
  await drag(x1, y1, x2, y2);
  for (let i = 0; i < 20 && (await allCount()) !== initial + 1; i++) await sleep(150);
  check("Rechteck angelegt", (await allCount()) === initial + 1, `${await allCount()} Fundstellen`);
  await screenshot("03-rechteck");

  // 4. Rückgängig / Wiederholen
  await key("z", { ctrl: true });
  await sleep(500);
  check("Strg+Z entfernt das Rechteck", (await allCount()) === initial);
  await key("y", { ctrl: true });
  await sleep(500);
  check("Strg+Y stellt es wieder her", (await allCount()) === initial + 1);

  // 5. Text auswählen (Taste T) über zwei Wörter
  await key("t");
  [x1, y1] = await pagePoint(110, 96);
  [x2, y2] = await pagePoint(185, 96);
  await drag(x1, y1, x2, y2);
  const textBar = await evaluate(`document.body.innerText.includes("Zur Schwärzung hinzufügen")`);
  check("Textauswahl zeigt Aktion", textBar);
  await clickButton("Zur Schwärzung hinzufügen");
  await sleep(600);
  check("Textmarkierung angelegt", (await allCount()) === initial + 2);

  // 6. Auswählen, Aktionsleiste, Verschieben
  await key("v");
  [x1, y1] = await pagePoint(120, 177);
  await clickAt(x1, y1);
  check("Aktionsleiste an der Markierung", await evaluate(`!!document.querySelector(".floating-bar[aria-label^='Aktionen']")`));
  await screenshot("04-auswahl");
  await drag(x1, y1, x1 + 30, y1 + 10);
  await sleep(400);
  const undoLabel = await evaluate(`[...document.querySelectorAll("button")].find(b => (b.getAttribute("aria-label") || "").startsWith("Rückgängig")).getAttribute("aria-label")`);
  check("Verschieben als Rückgängig-Schritt", undoLabel.includes("Markierung verändert"), undoLabel);

  // 7. Sichere Vorschläge bestätigen
  const bulk = await clickButton("sichere Vorschläge bestätigen");
  await sleep(800);
  check("Sammelbestätigung vorhanden und ausgeführt", bulk);

  // 8. Schwärzen über den Dialog
  await clickButton("Schwärzung anwenden");
  await waitFor(`!!document.querySelector(".modal")`, "Dialog");
  await screenshot("05-dialog");
  await clickButton("Jetzt schwärzen und prüfen");
  await waitFor(`document.body.innerText.includes("Automatische Prüfung ohne Befund") || document.body.innerText.includes("Export gesperrt")`, "Prüfergebnis");
  await sleep(1200);
  const passed = await evaluate(`document.body.innerText.includes("Automatische Prüfung ohne Befund")`);
  check("Schwärzung angewendet und Prüfung bestanden", passed);
  check("Export freigegeben", await evaluate(`[...document.querySelectorAll("button")].some(b => b.textContent.includes("Geschwärztes PDF speichern") && !b.disabled)`));
  check("Vergleichsansicht", await evaluate(`document.querySelectorAll(".compare-image").length === 2`));
  await screenshot("06-ergebnis");

  // 9. Dunkelmodus
  for (let i = 0; i < 3 && (await evaluate(`document.documentElement.dataset.theme`)) !== "dark"; i++) {
    await evaluate(`document.querySelector("button[aria-label*='Darstellung']").click()`);
    await sleep(200);
  }
  check("Dunkelmodus umschaltbar", (await evaluate(`document.documentElement.dataset.theme`)) === "dark");
  await screenshot("07-ergebnis-dunkel");
  await clickButton("Zurück zur Bearbeitung");
  await sleep(800);
  await screenshot("08-arbeitsbereich-dunkel");

  // 10. Nach einer Änderung ist das Ergebnis ungültig, Export gesperrt
  const firstCard = await evaluate(`(() => { const b = [...document.querySelectorAll(".mark-card button")].find(b => b.textContent === "Verwerfen"); if (!b) return false; b.click(); return true; })()`);
  await sleep(800);
  check("Änderung sperrt den Export wieder", firstCard && (await evaluate(`[...document.querySelectorAll("button")].some(b => b.getAttribute("aria-label")?.startsWith("Exportieren – gesperrt"))`)));

  // 11. Erneut analysieren: manuelle Markierungen bleiben erhalten
  const manualBefore = await evaluate(`[...document.querySelectorAll(".origin-badge")].filter(b => b.textContent === "Manuell").length`);
  await clickButton("Erneut analysieren");
  await waitFor(`!!document.querySelector(".modal")`, "Dialog Erneut analysieren");
  await screenshot("09-erneut-analysieren");
  await clickButton("Analyse starten");
  await waitFor(`!document.querySelector(".modal") && !!document.querySelector(".page-surface") && !document.body.innerText.includes("erneut analysiert")`, "Erneute Analyse fertig");
  await sleep(600);
  const manualAfter = await evaluate(`[...document.querySelectorAll(".origin-badge")].filter(b => b.textContent === "Manuell").length`);
  check("Erneut analysieren behält manuelle Markierungen", manualBefore >= 2 && manualAfter === manualBefore, `${manualBefore} → ${manualAfter}`);

  check("Keine Fehler in der Browser-Konsole", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
} catch (e) {
  check("Ablauf vollständig", false, e.message);
  await screenshot("fehler").catch(() => undefined);
} finally {
  writeFileSync(join(outDir, "ergebnis.json"), JSON.stringify({ results, consoleErrors }, null, 1));
  ws.close();
  edge?.kill();
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed} von ${results.length} Prüfungen bestanden`);
  process.exit(failed ? 1 : 0);
}
