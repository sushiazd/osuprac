@echo off
rem Double-clic : installe tout la premiere fois, puis ouvre le tableau de bord.
cd /d "%~dp0"
set "PYDIR=%LOCALAPPDATA%\Programs\Python\Python312"

if "%~1"=="apres-maj" goto deps_check
rem Mise a jour automatique (silencieuse hors-ligne), puis relance de ce fichier : il a pu etre remplace.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\update.py
    start "" "%~f0" apres-maj
    exit
)
:deps_check
if exist ".venv\Scripts\python.exe" goto deps

call :find_python
if not defined PYEXE call :install_python
if not defined PYEXE goto no_python

echo Premiere installation : creation de l'environnement Python...
"%PYEXE%" %PYARGS% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto no_python

:deps
if exist ".venv\Scripts\streamlit.exe" goto lazer
echo Installation des dependances, 1 a 2 minutes...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto deps_failed

:lazer
rem Lecteur des parties locales de lazer (optionnel, demande Node.js) : installe une seule fois.
if exist "tools\lazer-export\node_modules\realm" goto dotnet
if not exist "%APPDATA%\osu\client.realm" goto dotnet
where node >nul 2>nul || (
    echo Astuce : installe Node.js ^(https://nodejs.org^) pour que l'outil lise aussi tes parties locales de lazer.
    goto dotnet
)
echo Installation du lecteur de parties lazer, une seule fois...
pushd tools\lazer-export
call npm install --omit=dev --no-audit --no-fund --loglevel=error
rem Le paquet pese ~680 Mo, presque tout pour iPhone et Android : on ne garde que la version Windows (~9 Mo).
node -e "const fs=require('fs'),p=require('path'),d=p.join('node_modules','realm','prebuilds');if(fs.existsSync(d))for(const n of fs.readdirSync(d))if(n!=='node')fs.rmSync(p.join(d,n),{recursive:true,force:true})"
popd

:dotnet
rem Calcul des pp et des etoiles officiels d'osu! (tools\ppcalc) : il lui faut le runtime .NET 8, installe une seule fois
rem dans le dossier utilisateur (~30 Mo, sans droits administrateur). Sans lui, l'outil utilise un calcul approche.
if not exist "tools\ppcalc\ppcalc.dll" goto env
".venv\Scripts\python.exe" -c "import sys; from osuprog.ppcalc import dotnet_exe; sys.exit(0 if dotnet_exe() else 1)" 2>nul && goto env
echo Installation du runtime .NET 8 pour le calcul des pp, une seule fois...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $s=Join-Path $env:TEMP 'dotnet-install.ps1'; Invoke-WebRequest https://dot.net/v1/dotnet-install.ps1 -OutFile $s; & $s -Runtime dotnet -Channel 8.0 -InstallDir (Join-Path $env:USERPROFILE '.dotnet') -NoPath"

:env
if exist ".env" goto run
copy ".env.example" ".env" >nul
echo.
echo Remplis le fichier .env qui vient de s'ouvrir : ID client, secret client et pseudo osu!.
echo Le README explique comment creer ta cle API. Enregistre, puis relance ce fichier.
start "" notepad ".env"
pause
exit /b 0

:run
rem Ferme les anciens serveurs du tableau de bord : on repart toujours sur la derniere version du code.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'streamlit.exe','python.exe' -and $_.CommandLine -like '*%~dp0.venv*streamlit*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
".venv\Scripts\streamlit.exe" run app.py --server.port 8501
exit /b

:find_python
set "PYEXE="
set "PYARGS="
py -3 --version >nul 2>nul && (set "PYEXE=py" & set "PYARGS=-3" & exit /b)
python --version >nul 2>nul && (set "PYEXE=python" & exit /b)
if exist "%PYDIR%\python.exe" set "PYEXE=%PYDIR%\python.exe"
exit /b

:install_python
echo.
echo Python n'est pas installe sur ce PC.
where winget >nul 2>nul || exit /b
choice /C ON /M "Installer Python 3.12 maintenant avec winget, sans droits admin ? O = oui, N = non"
if errorlevel 2 exit /b
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if exist "%PYDIR%\python.exe" set "PYEXE=%PYDIR%\python.exe"
exit /b

:no_python
echo.
echo Python est introuvable. Installe Python 3.12 depuis la page qui vient de s'ouvrir,
echo en cochant "Add python.exe to PATH" en bas de la premiere fenetre, puis relance ce fichier.
start "" https://www.python.org/downloads/
pause
exit /b 1

:deps_failed
echo L'installation des dependances a echoue.
echo Si l'erreur parle d'un chemin introuvable, le dossier est trop profond pour Windows :
echo deplace-le plus pres de la racine, par exemple C:\osu-progression, puis relance.
pause
exit /b 1
