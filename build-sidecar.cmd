@echo off
rem ------------------------------------------------------------------
rem  Baut den Python-Sidecar als eigenstaendige .exe (PyInstaller).
rem  Ergebnis: sidecar\dist\redaction-sidecar\redaction-sidecar.exe
rem  Die Modelle kommen seit v34 NICHT mehr in diesen Ordner (sonst wird
rem  der Installer zu gross), siehe build-installer.cmd.
rem ------------------------------------------------------------------
setlocal
rem Phase 5: Python der Projekt-.venv bevorzugen ("python" im Pfad ist auf manchen
rem Rechnern nur der Microsoft-Store-Platzhalter).
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
cd /d "%~dp0sidecar"

echo.
echo === Sidecar 1/3: Pruefe Python-Pakete ===
"%PY%" build_check.py
if errorlevel 1 goto :fail

echo === Sidecar 2/3: Baue die .exe mit PyInstaller (dauert einige Minuten) ===
"%PY%" -m PyInstaller --noconfirm --clean redaction-sidecar.spec
if errorlevel 1 goto :fail

set "OUT=%~dp0sidecar\dist\redaction-sidecar"

echo.
echo === Sidecar 3/3: Kopiere Tesseract-OCR mit deutschem Sprachpaket ===
set "TESS=C:\Program Files\Tesseract-OCR"
if not exist "%TESS%\tesseract.exe" (
    echo   Tesseract nicht gefunden unter %TESS% - uebersprungen.
    goto :done
)
rem Alles ausser den Sprachdateien und der Dokumentation ...
robocopy "%TESS%" "%OUT%\vendor\tesseract" /E /NFL /NDL /NJH /NJS /NP /XF *.traineddata tesseract-uninstall.exe /XD doc >nul
if errorlevel 8 goto :fail
rem ... dann nur Deutsch, Englisch und die Ausrichtungserkennung (osd).
for %%L in (deu eng osd) do (
    if exist "%TESS%\tessdata\%%L.traineddata" copy /Y "%TESS%\tessdata\%%L.traineddata" "%OUT%\vendor\tesseract\tessdata\" >nul
)
rem Phase 5: fehlt Deutsch in der Tesseract-Installation, das Paket aus
rem sidecar\models\tessdata (tessdata_fast, Apache-2.0) nehmen.
if not exist "%OUT%\vendor\tesseract\tessdata\deu.traineddata" if exist "%~dp0sidecar\models\tessdata\deu.traineddata" copy /Y "%~dp0sidecar\models\tessdata\deu.traineddata" "%OUT%\vendor\tesseract\tessdata\" >nul
if not exist "%OUT%\vendor\tesseract\tessdata\deu.traineddata" (
    echo   WARNUNG: deu.traineddata fehlt - deutsches Sprachpaket in Tesseract nachinstallieren.
) else (
    echo   Tesseract mit Sprachen deu, eng, osd kopiert.
)

:done
echo.
echo   Sidecar fertig: %OUT%\redaction-sidecar.exe
exit /b 0

:fail
echo.
echo *** Abbruch beim Bauen des Sidecars: siehe Fehlermeldung oben. ***
exit /b 1
