# Reconstruit tools\ppcalc (le calculateur de pp officiel) depuis le code d'osu!.
# A relancer quand osu! change son calcul de pp : mettre a jour $Commit (et Program.Commit) avec le commit
# du classement « Live » de pp.huismetbenen.nl (https://api.pp.huismetbenen.nl/reworks/list, code "live").
# Usage : powershell -ExecutionPolicy Bypass -File tools\ppcalc-src\build.ps1
param(
    [string]$Commit = "b11b274d1cb5c22eabe9dba5df14fa1e4ecc4e6d",
    [string]$Work = "$env:USERPROFILE\osu-pp-build"
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$out = Join-Path (Split-Path $here) "ppcalc"
$dotnetRoot = "$env:USERPROFILE\.dotnet"
$dotnet = Join-Path $dotnetRoot "dotnet.exe"

if (-not (Test-Path $dotnet)) {
    Write-Host "Installation du SDK .NET 8 dans $dotnetRoot..."
    $script = Join-Path $env:TEMP "dotnet-install.ps1"
    Invoke-WebRequest https://dot.net/v1/dotnet-install.ps1 -OutFile $script
    & $script -Channel 8.0 -InstallDir $dotnetRoot -NoPath
}

New-Item -ItemType Directory -Force $Work | Out-Null
$src = Join-Path $Work "osu"
if (-not (Test-Path (Join-Path $src ".git"))) { git init -q $src; git -C $src remote add origin https://github.com/ppy/osu.git }
git -C $src fetch -q --depth 1 origin $Commit
git -C $src checkout -q FETCH_HEAD

$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:DOTNET_NOLOGO = "1"
$env:NUGET_PACKAGES = Join-Path $Work "nuget"
$build = Join-Path $Work "ppcalc-bin"
& $dotnet build "$here\ppcalc.csproj" -c Release -o $build -p:OsuSource=$src -v q
if ($LASTEXITCODE -ne 0) { throw "La construction a echoue" }

# On ne garde que le necessaire au calcul : ni son, ni graphismes, ni ressources du jeu (~600 Mo -> ~36 Mo).
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory $out | Out-Null
Get-ChildItem $build -File | Where-Object { $_.Extension -notin ".pdb", ".xml" -and $_.Name -ne "osu.Game.Resources.dll" } |
    Copy-Item -Destination $out
Write-Host "Calculateur pret dans $out"
