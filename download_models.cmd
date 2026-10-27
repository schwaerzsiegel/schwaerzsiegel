@echo off
rem ------------------------------------------------------------------
rem  v33: Laedt GLiNER einmalig nach sidecar\models\hf-cache.
rem  Danach laeuft die App ohne Internet. Braucht einmal Internet.
rem ------------------------------------------------------------------
setlocal
rem Phase 5: Python der Projekt-.venv bevorzugen ("python" im Pfad ist auf manchen
rem Rechnern nur der Microsoft-Store-Platzhalter).
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
cd /d "%~dp0sidecar"
"%PY%" download_models.py %*
if errorlevel 1 (
    echo.
    echo *** Abbruch: siehe Fehlermeldung oben. ***
    exit /b 1
)
exit /b 0
