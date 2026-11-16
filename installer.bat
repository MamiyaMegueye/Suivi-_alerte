@echo off
chcp 65001 >nul
title Installation - Alerte Baisse de Facturation
cd /d "%~dp0"

echo ============================================================
echo  Installation - Alerte Baisse de Facturation
echo  Application autonome : son propre environnement Python.
echo ============================================================
echo.

rem ---- 1. Environnement virtuel dedie ----
if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creation de l'environnement virtuel...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo  [!] Echec : Python n'est pas dans le PATH.
        echo      Installe Python 3.11+ ou lance cette commande a la main :
        echo         python -m venv .venv
        pause
        exit /b 1
    )
) else (
    echo [1/4] Environnement virtuel deja present.
)

set "PY=.venv\Scripts\python.exe"

rem ---- 2. Dependances ----
echo [2/4] Installation des dependances...
"%PY%" -m pip install --upgrade pip --quiet
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  [!] Echec de l'installation des dependances.
    pause
    exit /b 1
)

rem ---- 3. Fichier .env ----
if not exist ".env" (
    echo [3/4] Creation du fichier .env...
    copy ".env.example" ".env" >nul
    echo.
    echo  ============================================================
    echo   ACTION REQUISE
    echo  ============================================================
    echo   Le fichier .env vient d'etre cree. Il faut y mettre tes
    echo   identifiants Oracle, de l'une de ces deux facons :
    echo.
    echo   a^) Recopier ceux du dashboard - une seule commande :
    echo      copy "C:\Users\P C\Desktop\version_d\Dashboard-main\Dashboard-main\backend\.env" .env
    echo.
    echo   b^) Ou ouvrir .env dans le Bloc-notes et remplir
    echo      ORACLE_USER et ORACLE_PASSWORD.
    echo  ============================================================
    echo.
    set /p OUVRIR="Ouvrir .env dans le Bloc-notes maintenant ? (o/n) "
    if /i "%OUVRIR%"=="o" notepad .env
) else (
    echo [3/4] Fichier .env deja present.
)

rem ---- 4. Test de connexion ----
echo.
echo [4/4] Test de la connexion Oracle...
"%PY%" -c "from src import oracle_client; r=oracle_client.tester(); print('   Oracle :', 'OK' if r.get('ok') else 'ECHEC - '+str(r.get('erreur')))"

echo.
echo ============================================================
echo  Installation terminee.
echo.
echo  Etape suivante - chargement initial des donnees
echo  (quelques minutes, selon la profondeur d'historique^) :
echo.
echo      "%PY%" -m src.etl
echo.
echo  Puis double-clic sur lancer.bat
echo ============================================================
echo.
pause
