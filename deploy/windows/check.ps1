param(
    [string]$InstallDir = "",
    [string]$HermesHome = ""
)

$ErrorActionPreference = "Continue"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$statePath = Join-Path $projectRoot ".install-state.json"
if (-not (Test-Path -LiteralPath $statePath)) {
    $defaultStatePath = Join-Path $env:LOCALAPPDATA "MedicalLiteratureTracker\.install-state.json"
    if (Test-Path -LiteralPath $defaultStatePath) { $statePath = $defaultStatePath }
}
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath | ConvertFrom-Json
    if (-not $InstallDir) { $InstallDir = $state.install_dir }
    if (-not $HermesHome) { $HermesHome = $state.hermes_home }
}
if (-not $InstallDir) { $InstallDir = $projectRoot }
if (-not $HermesHome) { $HermesHome = "$env:LOCALAPPDATA\hermes" }

$env:HERMES_HOME = $HermesHome
$python = Join-Path $HermesHome "hermes-agent\venv\Scripts\python.exe"
$portable = Join-Path $InstallDir "hermes\portable.py"

if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "Hermes Python was not found: $python"
    exit 1
}
if (-not (Test-Path -LiteralPath $portable)) {
    Write-Error "Tracker was not found: $InstallDir"
    exit 1
}

& $python $portable doctor
exit $LASTEXITCODE
