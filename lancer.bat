@echo off
REM Lance le scraper sous Windows : cree l'environnement virtuel au premier lancement,
REM installe les dependances puis execute main.py avec les arguments passes au script.
REM Exemples :  lancer.bat            lancer.bat --skip-photos            lancer.bat --no-scrape
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
)
REM Installe ou met a jour les dependances a chaque lancement (rapide si rien ne manque).
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || (
    echo Echec de l'installation des dependances.
    pause
    exit /b 1
)

.venv\Scripts\python.exe main.py %*
