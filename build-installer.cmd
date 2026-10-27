@echo off
rem ------------------------------------------------------------------
rem  v34: Baut die komplette Desktop-App in einem Rutsch.
rem    1. Voraussetzungen pruefen
rem    2. GLiNER herunterladen, falls noch nicht geschehen
rem    3. Python-Sidecar als .exe (build-sidecar.cmd) - wird uebersprungen,
rem       wenn schon gebaut. Neu bauen erzwingen: build-installer.cmd neu
rem    4. Tauri-Installer
rem    5. Installationspaket zusammenstellen: release\Schwaerzsiegel
rem       = Installer + Ordner "models" daneben
rem  Die Modelle (ca. 3,5 GB) stecken bewusst nicht im Installer selbst:
rem  NSIS-Installer duerfen hoechstens 2 GB gross sein. Der Installer
rem  kopiert sie bei der Installation aus dem Nachbarordner "models"
rem  (src-tauri\installer-hooks.nsh).
rem ------------------------------------------------------------------
setlocal
rem Phase 5: Python der Projekt-.venv bevorzugen ("python" im Pfad ist auf manchen
rem Rechnern nur der Microsoft-Store-Platzhalter).
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
cd /d "%~dp0"
set "ROOT=%~dp0"

echo.
echo ===== 1/5 Voraussetzungen =====
"%PY%" --version >nul 2>&1 || (echo   FEHLT: Python ^(.venv oder Python im PATH^) & goto :fail)
where npm >nul 2>&1 || (echo   FEHLT: Node.js / npm & goto :fail)
where cargo >nul 2>&1 || (echo   FEHLT: Rust - siehe Anleitung, Schritt "Einmalig installieren" & goto :fail)
"%PY%" -c "import PyInstaller" >nul 2>&1 || (echo   FEHLT: PyInstaller in der .venv - "%PY%" -m pip install pyinstaller & goto :fail)
rem Qwen ist optional: ohne .gguf funktioniert nur der Modus "Schnell"
rem (Regeln + GLiNER), "Gruendlich mit KI" meldet dann einen Fehler.
if exist "%ROOT%sidecar\models\*.gguf" (
    echo   Python, npm, Rust und Qwen-Modell gefunden.
) else (
    echo   Python, npm und Rust gefunden.
    echo   HINWEIS: Kein Qwen-Modell ^(.gguf^) in sidecar\models - nur Modus "Schnell" verfuegbar.
)

echo.
echo ===== 2/5 GLiNER fuer den Offline-Betrieb =====
if exist "%ROOT%sidecar\models\hf-cache" (
    echo   Schon vorhanden.
) else (
    call "%ROOT%download_models.cmd"
    if errorlevel 1 goto :fail
)

echo.
echo ===== 3/5 Python-Sidecar als .exe =====
if exist "%ROOT%sidecar\dist\redaction-sidecar\redaction-sidecar.exe" if /I not "%~1"=="neu" (
    echo   Schon gebaut, wird wiederverwendet. Neu bauen: build-installer.cmd neu
    goto :trim
)
call "%ROOT%build-sidecar.cmd"
if errorlevel 1 goto :fail
:trim
rem v35: zu lange Pfade entfernen/pruefen (makensis schafft max. 260 Zeichen)
"%PY%" "%ROOT%sidecar\trim_dist.py"
if errorlevel 1 goto :fail
rem Lizenzhinweise (AGPL-Text, Drittanbieter-Lizenzen, Bausteinliste)
"%PY%" "%ROOT%tools\lizenzhinweise.py"
if errorlevel 1 goto :fail

echo.
echo ===== 4/5 Desktop-App und Installer (beim ersten Mal 10-20 Minuten) =====
cd /d "%ROOT%"
call npm install
if errorlevel 1 goto :fail
call npm run tauri:build
if errorlevel 1 goto :fail

echo.
echo ===== 5/5 Installationspaket zusammenstellen =====
set "REL=%ROOT%release\Schwaerzsiegel"
if exist "%REL%" rmdir /S /Q "%REL%"
mkdir "%REL%\models"
rem Phase 5: nur den neuesten Installer - aeltere Versionen bleiben sonst im
rem Bundle-Ordner liegen und landeten mit im Installationspaket.
set "SETUP="
for /f "delims=" %%F in ('dir /b /o-d "%ROOT%src-tauri\target\release\bundle\nsis\*-setup.exe" 2^>nul') do if not defined SETUP set "SETUP=%%F"
if not defined SETUP goto :fail
copy /Y "%ROOT%src-tauri\target\release\bundle\nsis\%SETUP%" "%REL%\" >nul
if errorlevel 1 goto :fail
echo   Installer: %SETUP%
rem Phase 11: vollstaendiger Quellcode (AGPL) als ZIP neben das Installationsprogramm.
rem Das Werkzeug gehoert zum internen Arbeitsprojekt; im oeffentlichen Quellcode fehlt es.
if exist "%ROOT%tools\veroeffentlichen\quellcode_zip.py" (
    "%PY%" "%ROOT%tools\veroeffentlichen\quellcode_zip.py" "%REL%"
    if errorlevel 1 goto :fail
)
for %%F in ("%ROOT%sidecar\models\*.gguf") do (
    rem Harter Link statt Kopie: belegt keinen zusaetzlichen Speicher.
    mklink /H "%REL%\models\%%~nxF" "%%~fF" >nul 2>&1 || copy /Y "%%~fF" "%REL%\models\" >nul
)
rem GLiNER ohne "blobs": die Dateien stehen in "snapshots" vollstaendig drin.
rem Phase 5: pytorch_model.bin weglassen - das Modell liegt zusaetzlich als
rem model.safetensors vor, GLiNER laedt damit identisch (getestet), spart 1,1 GB.
robocopy "%ROOT%sidecar\models\hf-cache" "%REL%\models\hf-cache" /E /NFL /NDL /NJH /NJS /NP /XD blobs .locks /XF pytorch_model.bin >nul
if errorlevel 8 goto :fail
rem Phase 5: Kunden-Branding (sidecar\branding, Vorlage sidecar\branding-beispiel) mitliefern
if exist "%ROOT%sidecar\branding\branding.json" (
    robocopy "%ROOT%sidecar\branding" "%REL%\branding" /E /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :fail
    echo   Branding aus sidecar\branding wird mitgeliefert.
)

echo.
echo ==================================================================
echo  Fertig! Installationspaket: %REL%
echo  Zum Installieren dort die *-setup.exe doppelklicken.
echo  Der Ordner "models" muss dabei neben der setup.exe liegen.
echo ==================================================================
explorer "%REL%"
exit /b 0

:fail
echo.
echo *** Abbruch: siehe Fehlermeldung oben. Bitte Screenshot davon schicken. ***
exit /b 1
