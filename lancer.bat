@echo off
chcp 65001 >nul
title SNDE - Alerte Baisse de Facturation
cd /d "%~dp0"

rem ============================================================
rem  SNDE - Alerte Baisse de Facturation
rem  Application autonome : rien de partage avec le dashboard.
rem ============================================================

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    echo.
    echo  [!] Environnement virtuel absent.
    echo      Lance d'abord installer.bat
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo.
    echo  [!] Fichier .env absent - identifiants Oracle manquants.
    echo      Lance d'abord installer.bat
    echo.
    pause
    exit /b 1
)

if not exist "data\baisse.duckdb" (
    echo.
    echo  Base locale absente : chargement initial depuis Oracle.
    echo  Cela peut prendre quelques minutes.
    echo.
    "%PY%" -m src.etl
    if errorlevel 1 (
        echo.
        echo  [!] Le chargement a echoue. Verifie le .env et le reseau.
        pause
        exit /b 1
    )
    echo.
)

echo ============================================================
echo  SNDE - Alerte Baisse de Facturation
echo ============================================================
echo.
echo  Interface : http://localhost:8020
echo  Veille Oracle : active (voir WATCHER_INTERVAL_SEC dans .env)
echo.
echo  Pour arreter : Ctrl+C
echo.

start "" http://localhost:8020
"%PY%" -m uvicorn src.api:app --host 0.0.0.0 --port 8020

pause
