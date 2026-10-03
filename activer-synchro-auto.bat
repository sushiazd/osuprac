@echo off
rem Cree une tache Windows qui synchronise tes parties toutes les heures, sans ouvrir de fenetre.
rem Pour la supprimer : schtasks /Delete /TN "osu-progression sync" /F
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Lance d'abord lancer.bat une fois pour installer l'outil.
    pause
    exit /b 1
)
schtasks /Create /F /SC HOURLY /TN "osu-progression sync" /TR "\"%~dp0.venv\Scripts\pythonw.exe\" \"%~dp0tools\sync_auto.pyw\""
if errorlevel 1 (
    echo La creation de la tache a echoue.
    pause
    exit /b 1
)
echo.
echo Synchro automatique activee : toutes les heures tant que ton PC est allume et ta session ouverte.
echo Le journal est dans data\sync.log.
pause
