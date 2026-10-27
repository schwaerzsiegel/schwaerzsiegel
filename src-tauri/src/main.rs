// Startet den Python-Sidecar-Prozess und stellt dem Frontend dessen
// lokalen Port zur Verfügung. Der Sidecar bindet ausschließlich an
// 127.0.0.1 - die CSP in tauri.conf.json erlaubt ohnehin keine anderen
// Verbindungen, sodass ein versehentlicher Cloud-Call architektonisch
// ausgeschlossen ist.
//
// v32: Der Sidecar meldet seinen Port als Zeile "SIDECAR_PORT=<port>" auf
// stdout (statt über eine Port-Datei, deren Pfad bisher nicht zu dem des
// Sidecars passte). Er wird mit --exit-with-parent gestartet und beim
// Schließen der App zusätzlich ausdrücklich beendet.
//
// - `npm run tauri dev` (Debug-Build): startet `python main.py` im Ordner
//   sidecar, wie bisher in der Entwicklung.
// - Fertige App (Release-Build): startet die mit build-sidecar.cmd gebaute
//   redaction-sidecar.exe aus dem Ressourcen-Ordner der Installation.

// Kein zusätzliches Konsolenfenster für die fertige App unter Windows
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{Emitter, Manager, RunEvent, State};

struct SidecarPort(Mutex<Option<u16>>);
struct SidecarToken(String);
struct SidecarChild(Mutex<Option<Child>>);
// Grund, warum der Sidecar nicht verfügbar ist (Start fehlgeschlagen oder
// beendet, bevor er seinen Port gemeldet hat). Ohne das wartete das
// Frontend endlos bei "KI-Dienst wird gestartet ...".
struct SidecarError(Mutex<Option<String>>);
#[tauri::command]
fn get_sidecar_token(state: State<SidecarToken>) -> String {
    state.0.clone()
}

#[tauri::command]
fn get_sidecar_error(state: State<SidecarError>) -> Option<String> {
    state.0.lock().unwrap().clone()
}

#[tauri::command]
fn get_sidecar_port(state: State<SidecarPort>) -> Option<u16> {
    *state.0.lock().unwrap()
}

fn sidecar_command(app: &tauri::AppHandle) -> Command {
    if cfg!(debug_assertions) {
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("sidecar");
        // Phase 5: Python der Projekt-.venv bevorzugen ("python" im Pfad ist
        // unter Windows oft nur der Microsoft-Store-Platzhalter)
        let venv = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join(".venv")
            .join("Scripts")
            .join("python.exe");
        let python = if venv.is_file() {
            venv.to_string_lossy().into_owned()
        } else if cfg!(windows) {
            "python".to_string()
        } else {
            "python3".to_string()
        };
        let mut cmd = Command::new(python);
        cmd.arg("main.py").current_dir(dir);
        cmd
    } else {
        let dir = app
            .path()
            .resource_dir()
            .expect("Ressourcen-Ordner nicht verfügbar")
            .join("sidecar");
        let exe = if cfg!(windows) {
            "redaction-sidecar.exe"
        } else {
            "redaction-sidecar"
        };
        let mut cmd = Command::new(dir.join(exe));
        cmd.current_dir(dir);
        cmd
    }
}

/// Phase 5: Fenstertitel aus dem Kunden-Branding (sidecar/branding/branding.json,
/// dieselbe Datei, die der Sidecar liest). Fehlt sie oder ist sie ungültig,
/// bleibt der Standardtitel. Nur Text, gekürzt und ohne Steuerzeichen.
fn branded_title(app: &tauri::AppHandle) -> Option<String> {
    let dir = if let Ok(custom) = std::env::var("REDACTION_BRANDING_DIR") {
        PathBuf::from(custom)
    } else if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("sidecar")
            .join("branding")
    } else {
        app.path()
            .resource_dir()
            .ok()?
            .join("sidecar")
            .join("branding")
    };
    let raw = std::fs::read_to_string(dir.join("branding.json")).ok()?;
    if raw.len() > 64 * 1024 {
        return None;
    }
    let data: serde_json::Value = serde_json::from_str(raw.trim_start_matches('\u{feff}')).ok()?;
    let clean = |key: &str, max: usize| -> Option<String> {
        let text: String = data
            .get(key)?
            .as_str()?
            .chars()
            .filter(|c| !c.is_control())
            .take(max)
            .collect();
        let text = text.trim().to_string();
        (!text.is_empty()).then_some(text)
    };
    let product = clean("productName", 60).unwrap_or_else(|| "Schwärzsiegel".to_string());
    Some(match clean("customerName", 80) {
        Some(customer) => format!("{product} – {customer}"),
        None => product,
    })
}

fn main() {
    use rand::RngCore;
    let mut bytes = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    let session_token: String = bytes.iter().map(|b| format!("{:02x}", b)).collect();
    let app = tauri::Builder::default()
        .manage(SidecarPort(Mutex::new(None)))
        .manage(SidecarToken(session_token.clone()))
        .manage(SidecarChild(Mutex::new(None)))
        .manage(SidecarError(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            if let (Some(title), Some(window)) =
                (branded_title(&handle), app.get_webview_window("main"))
            {
                let _ = window.set_title(&title);
            }
            start_sidecar(&handle);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_sidecar_port,
            get_sidecar_token,
            get_sidecar_error
        ])
        .build(tauri::generate_context!())
        .expect("Fehler beim Starten der App");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<SidecarChild>().0.lock().unwrap().take() {
                // Phase 10: Erst die Eingabeleitung schließen. Der Sidecar
                // beendet sich dann selbst und löscht dabei seinen
                // Arbeitsordner mit temporären Dateien. Nur wenn er nicht
                // binnen 3 s endet, wird er hart beendet (die Reste löscht
                // dann der nächste Start).
                drop(child.stdin.take());
                let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
                while std::time::Instant::now() < deadline {
                    match child.try_wait() {
                        Ok(Some(_)) => return,
                        Ok(None) => std::thread::sleep(std::time::Duration::from_millis(50)),
                        Err(_) => break,
                    }
                }
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}

/// Startet den Sidecar (einmal beim App-Start).
fn start_sidecar(handle: &tauri::AppHandle) {
    let handle = handle.clone();
    let data_dir = handle
        .path()
        .app_data_dir()
        .expect("App-Datenverzeichnis nicht verfügbar");
    {
        let mut cmd = sidecar_command(&handle);
        let token = handle.state::<SidecarToken>().0.clone();
        cmd.arg("--exit-with-parent")
            .env("REDACTION_APP_DATA_DIR", &data_dir)
            .env("REDACTION_SESSION_TOKEN", token)
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUNBUFFERED", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped());
        // Fertige App: kein Terminal, stderr (Python-Tracebacks beim
        // Start, z.B. fehlende DLLs) sonst verloren -> in eine Datei.
        let stderr_log = data_dir.join("sidecar-stderr.log");
        let stderr_file = if cfg!(debug_assertions) {
            None
        } else {
            std::fs::create_dir_all(&data_dir)
                .and_then(|_| std::fs::File::create(&stderr_log))
                .ok()
        };
        match stderr_file {
            Some(file) => cmd.stderr(Stdio::from(file)),
            None => cmd.stderr(Stdio::inherit()),
        };
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = match cmd.spawn() {
            Ok(child) => child,
            Err(e) => {
                *handle.state::<SidecarError>().0.lock().unwrap() =
                    Some(format!("Der KI-Dienst konnte nicht gestartet werden: {e}"));
                return;
            }
        };
        let stdout = child
            .stdout
            .take()
            .expect("Sidecar-Ausgabe nicht verfügbar");
        *handle.state::<SidecarChild>().0.lock().unwrap() = Some(child);

        // Port aus der Ausgabe lesen, restliche Zeilen ins Terminal
        // durchreichen (nur in der Entwicklung sichtbar).
        std::thread::spawn(move || {
            let mut found_port = false;
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Some(port) = line
                    .strip_prefix("SIDECAR_PORT=")
                    .and_then(|p| p.trim().parse::<u16>().ok())
                {
                    found_port = true;
                    *handle.state::<SidecarPort>().0.lock().unwrap() = Some(port);
                    handle.emit("sidecar-ready", port).ok();
                } else {
                    println!("[sidecar] {line}");
                }
            }
            // stdout geschlossen = Sidecar beendet. Vor der Port-Meldung
            // heißt das: Start fehlgeschlagen.
            if !found_port {
                std::thread::sleep(std::time::Duration::from_millis(300));
                let code = handle
                    .state::<SidecarChild>()
                    .0
                    .lock()
                    .unwrap()
                    .as_mut()
                    .and_then(|c| c.try_wait().ok().flatten())
                    .and_then(|s| s.code())
                    .map(|c| format!(" (Exit-Code {c})"))
                    .unwrap_or_default();
                *handle.state::<SidecarError>().0.lock().unwrap() = Some(format!(
                        "Der KI-Dienst wurde beim Start unerwartet beendet{code}.                          Details stehen in {}",
                        stderr_log.display()
                    ));
            }
        });
    }
}
