param(
    [Parameter(Mandatory = $true)][string]$HermesHome,
    [Parameter(Mandatory = $true)][string]$HermesExe
)

$ErrorActionPreference = "SilentlyContinue"
$env:HERMES_HOME = $HermesHome
$logDir = Join-Path $HermesHome "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "medical-literature-tracker-watchdog.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

if (-not (Test-Path -LiteralPath $HermesExe)) {
    Add-Content -LiteralPath $log -Value "$stamp Hermes executable is missing: $HermesExe"
    exit 1
}

$output = & $HermesExe gateway start 2>&1 | Out-String
$code = $LASTEXITCODE
Add-Content -LiteralPath $log -Value "$stamp exit=$code $($output.Trim())"

if ((Get-Item -LiteralPath $log).Length -gt 1048576) {
    $lines = Get-Content -LiteralPath $log -Tail 500
    [IO.File]::WriteAllLines($log, $lines, [Text.UTF8Encoding]::new($false))
}

exit 0
