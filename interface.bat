@echo off
REM Ouvre l'interface web (http://127.0.0.1:5000). Cree l'environnement au premier lancement.
cd /d "%~dp0"

REM Python : lanceur "py" (installeur python.org) ou "python" (Microsoft Store).
set PYTHON=
where py >nul 2>nul && set PYTHON=py -3
if not defined PYTHON (
    where python >nul 2>nul && set PYTHON=python
)
if not defined PYTHON (
    echo Python est introuvable. Installez-le depuis https://www.python.org/downloads/windows/
    echo en cochant "Add python.exe to PATH", puis relancez ce script.
    pause
    exit /b 1
)

if not exist .venv (
    echo Creation de l'environnement virtuel...
    %PYTHON% -m venv .venv || exit /b 1
)
REM Installe ou met a jour les dependances a chaque lancement (rapide si rien ne manque).
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || (
    echo Echec de l'installation des dependances.
    pause
    exit /b 1
)

.venv\Scripts\python.exe app.py
pause
