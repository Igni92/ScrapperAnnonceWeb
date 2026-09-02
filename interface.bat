@echo off
REM Ouvre l'interface web (http://127.0.0.1:5000). Cree l'environnement au premier lancement.
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python est introuvable. Installez-le depuis https://www.python.org/downloads/windows/
    echo en cochant "Add python.exe to PATH", puis relancez ce script.
    pause
    exit /b 1
)

if not exist .venv (
    echo Creation de l'environnement virtuel...
    py -3 -m venv .venv || exit /b 1
    call .venv\Scripts\pip.exe install -r requirements.txt || exit /b 1
)

.venv\Scripts\python.exe app.py
pause
