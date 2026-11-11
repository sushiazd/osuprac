# Reconstruit « osu progression.exe » (le lanceur, a la racine du dossier) depuis Lanceur.cs.
# Utilise le compilateur C# fourni avec Windows (.NET Framework 4) : rien a installer.
# Usage : powershell -ExecutionPolicy Bypass -File tools\lanceur-src\build.ps1
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$root = Split-Path (Split-Path $here)
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $csc /nologo /target:winexe /optimize+ /codepage:65001 `
    "/win32icon:$root\assets\icon.ico" "/out:$root\osu progression.exe" `
    /reference:System.Windows.Forms.dll "$here\Lanceur.cs"
if ($LASTEXITCODE -ne 0) { throw "La compilation du lanceur a echoue." }
Write-Host "OK -> $root\osu progression.exe"
