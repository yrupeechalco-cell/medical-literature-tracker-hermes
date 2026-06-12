param([switch]$PurgeData)

$ErrorActionPreference = "Continue"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$statePath = Join-Path $projectRoot ".install-state.json"
if (-not (Test-Path -LiteralPath $statePath)) {
    $defaultStatePath = Join-Path $env:LOCALAPPDATA "MedicalLiteratureTracker\.install-state.json"
    if (Test-Path -LiteralPath $defaultStatePath) { $statePath = $defaultStatePath }
}
$installDir = $projectRoot
$hermesHome = "$env:LOCALAPPDATA\hermes"

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath | ConvertFrom-Json
    if ($state.install_dir) { $installDir = $state.install_dir }
    if ($state.hermes_home) { $hermesHome = $state.hermes_home }
}

$env:HERMES_HOME = $hermesHome
$python = Join-Path $hermesHome "hermes-agent\venv\Scripts\python.exe"
$portable = Join-Path $installDir "hermes\portable.py"

if ((Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $portable)) {
    & $python $portable uninstall
}
& schtasks.exe /Delete /TN "MedicalLiteratureTracker-HermesWatchdog" /F *> $null

if ($PurgeData) {
    $answer = Read-Host "Delete the complete tracker directory and all literature history? Type DELETE"
    if ($answer -eq "DELETE") {
        Write-Host "Close this window after deletion completes."
        $escapedInstallDir = $installDir.Replace("'", "''")
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
            "-NoProfile", "-Command",
            "Start-Sleep 2; Remove-Item -LiteralPath '$escapedInstallDir' -Recurse -Force"
        )
    } else {
        Write-Host "Data deletion cancelled."
    }
} else {
    Write-Host "Hermes integration removed. Literature data was preserved at: $installDir"
    Write-Host "Run with -PurgeData only when permanent deletion is intended."
}

exit 0
