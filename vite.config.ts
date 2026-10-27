import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { randomBytes } from "node:crypto";

// v32: Der Vite-Dev-Server startet den Python-Sidecar selbst mit, liest
// dessen Port aus der Zeile "SIDECAR_PORT=<port>" und stellt ihn dem
// Frontend unter /__sidecar/status bereit. Dadurch entfallen das zweite
// Terminalfenster und die Port-Eingabe im Browser.
//
//   npm run dev       -> startet `python main.py` im Ordner sidecar
//   npm run dev:exe   -> startet die gebaute sidecar\dist\redaction-sidecar\redaction-sidecar.exe
//
// In der fertigen Tauri-App übernimmt das src-tauri/src/main.rs.
function sidecarPlugin(useExe: boolean): Plugin {
  const sidecarDir = path.resolve(__dirname, "sidecar");
  const exeName = process.platform === "win32" ? "redaction-sidecar.exe" : "redaction-sidecar";
  const exePath = path.join(sidecarDir, "dist", "redaction-sidecar", exeName);

  let child: ChildProcess | null = null;
  let port: number | null = null;
  let error: string | null = null;
  const sessionToken = randomBytes(32).toString("hex");

  function start() {
    let command: string;
    let args: string[];
    if (useExe) {
      if (!existsSync(exePath)) {
        error = `Gebaute Sidecar-.exe nicht gefunden: ${exePath} - zuerst build-sidecar.cmd ausführen.`;
        console.error(`[sidecar] ${error}`);
        return;
      }
      command = exePath;
      args = ["--exit-with-parent"];
    } else {
      command = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
      args = ["main.py", "--exit-with-parent"];
    }

    console.log(`[sidecar] Starte ${useExe ? "gebaute .exe" : "python main.py"} ...`);
    child = spawn(command, args, {
      cwd: useExe ? path.dirname(exePath) : sidecarDir,
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        REDACTION_SESSION_TOKEN: sessionToken,
        PYTHONIOENCODING: "utf-8",
        PYTHONUNBUFFERED: "1",
        // Die gebaute .exe nimmt die Modelle aus sidecar\models (v34)
        ...(useExe ? { REDACTION_MODELS_DIR: path.join(sidecarDir, "models") } : {}),
      },
      windowsHide: true,
    });

    const forward = (data: Buffer) => {
      for (const line of data.toString("utf-8").split(/\r?\n/)) {
        if (!line.trim()) continue;
        const match = line.match(/^SIDECAR_PORT=(\d+)/);
        if (match) {
          port = Number(match[1]);
          error = null;
          console.log(`[sidecar] Läuft auf Port ${port}. Im Browser http://localhost:1420 öffnen.`);
          continue;
        }
        console.log(`[sidecar] ${line}`);
      }
    };
    child.stdout?.on("data", forward);
    child.stderr?.on("data", forward);

    child.on("error", (e) => {
      error = `Sidecar konnte nicht gestartet werden (${command}): ${e.message}`;
      console.error(`[sidecar] ${error}`);
    });
    child.on("exit", (code) => {
      if (code !== 0 && code !== null) {
        error = `Der KI-Dienst (Sidecar) wurde mit Code ${code} beendet. Details stehen im Terminalfenster.`;
        console.error(`[sidecar] ${error}`);
      }
      port = null;
      child = null;
    });
  }

  function stop() {
    child?.kill();
    child = null;
  }

  return {
    name: "redaction-sidecar",
    apply: "serve",
    configureServer(server) {
      start();
      server.httpServer?.on("close", stop);
      process.on("exit", stop);
      server.middlewares.use("/__sidecar/status", (_req, res) => {
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({ port, error, token: sessionToken }));
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  // Unter `npm run tauri dev` (mode "tauri") startet main.rs den Sidecar.
  plugins: [react(), ...(mode === "tauri" ? [] : [sidecarPlugin(mode === "exe")])],
  server: {
    port: 1420,
    strictPort: true,
    // Log, Audit-DB und Build-Ordner des Sidecars nicht beobachten
    watch: { ignored: ["**/sidecar/**", "**/src-tauri/**"] },
  },
}));
