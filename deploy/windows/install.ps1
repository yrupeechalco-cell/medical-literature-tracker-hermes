param(
    [string]$InstallDir = "$env:LOCALAPPDATA\MedicalLiteratureTracker",
    [string]$HermesHome = "$env:LOCALAPPDATA\hermes",
    [string]$Schedule = "30 7 * * *",
    [string]$Deliver = "",
    [string]$HermesTag = "v2026.6.5",
    [string]$UvVersion = "0.11.21",
    [switch]$SkipHermesInstall,
    [switch]$SkipCcSwitchInstall,
    [switch]$SkipFeishuSetup,
    [switch]$SkipGatewayService,
    [switch]$SkipEndToEndTest,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Utf8Lines([string]$Path, [string[]]$Lines) {
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    [IO.File]::WriteAllLines($Path, $Lines, [Text.UTF8Encoding]::new($false))
}

function Get-DotEnvValue([string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $prefix = "$Name="
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line.StartsWith($prefix)) { return $line.Substring($prefix.Length).Trim() }
    }
    return $null
}

function Set-DotEnvValue([string]$Path, [string]$Name, [string]$Value) {
    $lines = @()
    if (Test-Path -LiteralPath $Path) { $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8) }
    $prefix = "$Name="
    $updated = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index].StartsWith($prefix)) {
            $lines[$index] = "$Name=$Value"
            $updated = $true
        }
    }
    if (-not $updated) { $lines += "$Name=$Value" }
    Write-Utf8Lines -Path $Path -Lines $lines
}

function Read-Secret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Copy-Project([string]$Source, [string]$Destination) {
    $sourcePath = [IO.Path]::GetFullPath($Source).TrimEnd('\')
    $destinationPath = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
    if ($sourcePath -eq $destinationPath) { return }
    New-Item -ItemType Directory -Force -Path $destinationPath | Out-Null
    $excludedTop = @(".git", "data", "dist", "logs", "raw", "reports", "__pycache__")
    Get-ChildItem -LiteralPath $sourcePath -Force | Where-Object {
        $excludedTop -notcontains $_.Name
    } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $destinationPath -Recurse -Force
    }
}

function Add-WatchdogTask([string]$ProjectDir, [string]$Home, [string]$HermesExe) {
    $watchdog = Join-Path $ProjectDir "deploy\windows\gateway_watchdog.ps1"
    $taskName = "MedicalLiteratureTracker-HermesWatchdog"
    $taskCommand = "powershell.exe -NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdog`" -HermesHome `"$Home`" -HermesExe `"$HermesExe`""
    & schtasks.exe /Create /TN $taskName /SC MINUTE /MO 5 /TR $taskCommand /F /RL LIMITED | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not install the watchdog task. Hermes gateway install remains active."
    }
}

function Assert-FreeSpace([string]$Path, [long]$MinimumBytes) {
    $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
    $drive = Get-PSDrive -Name $root.Substring(0, 1)
    if ($drive.Free -lt $MinimumBytes) {
        throw "At least $([Math]::Round($MinimumBytes / 1GB, 1)) GB free space is required on $root"
    }
}

function Install-MinimalHermes(
    [string]$HermesRoot,
    [string]$Destination,
    [string]$Tag,
    [string]$PinnedUvVersion,
    [string]$PayloadDir
) {
    Write-Step "Installing minimal Hermes Agent $Tag"
    New-Item -ItemType Directory -Force -Path $HermesRoot | Out-Null
    $binDir = Join-Path $HermesRoot "bin"
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    $uvExe = Join-Path $binDir "uv.exe"

    if (-not (Test-Path -LiteralPath $uvExe)) {
        $architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
        $target = if ($architecture -eq "arm64") { "aarch64-pc-windows-msvc" } else { "x86_64-pc-windows-msvc" }
        $bundledUvZip = Join-Path $PayloadDir "uv-$PinnedUvVersion-$target.zip"
        $uvZip = if (Test-Path -LiteralPath $bundledUvZip) { $bundledUvZip } else { Join-Path $env:TEMP "uv-$PinnedUvVersion-$target.zip" }
        $uvExtract = Join-Path $env:TEMP "uv-$PinnedUvVersion-$target"
        Remove-Item -LiteralPath $uvExtract -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $bundledUvZip)) {
            Invoke-WebRequest -UseBasicParsing `
                -Uri "https://github.com/astral-sh/uv/releases/download/$PinnedUvVersion/uv-$target.zip" `
                -OutFile $uvZip
        }
        Expand-Archive -LiteralPath $uvZip -DestinationPath $uvExtract -Force
        Copy-Item -LiteralPath (Join-Path $uvExtract "uv.exe") -Destination $uvExe -Force
        $uvw = Join-Path $uvExtract "uvw.exe"
        if (Test-Path -LiteralPath $uvw) { Copy-Item -LiteralPath $uvw -Destination $binDir -Force }
    }

    $bundledSourceZip = Join-Path $PayloadDir "hermes-agent-$Tag.zip"
    $sourceZip = if (Test-Path -LiteralPath $bundledSourceZip) { $bundledSourceZip } else { Join-Path $env:TEMP "hermes-agent-$Tag.zip" }
    $sourceExtract = Join-Path $env:TEMP "hermes-agent-$Tag"
    Remove-Item -LiteralPath $sourceExtract -Recurse -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $bundledSourceZip)) {
        Invoke-WebRequest -UseBasicParsing `
            -Uri "https://github.com/NousResearch/hermes-agent/archive/refs/tags/$Tag.zip" `
            -OutFile $sourceZip
    }
    Expand-Archive -LiteralPath $sourceZip -DestinationPath $sourceExtract -Force
    $source = Get-ChildItem -LiteralPath $sourceExtract -Directory | Select-Object -First 1
    if (-not $source) { throw "The Hermes release archive did not contain a source directory." }
    if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Recurse -Force }
    Move-Item -LiteralPath $source.FullName -Destination $Destination

    & $uvExe python install 3.11
    if ($LASTEXITCODE -ne 0) { throw "uv could not install Python 3.11." }
    & $uvExe venv (Join-Path $Destination "venv") --python 3.11
    if ($LASTEXITCODE -ne 0) { throw "uv could not create the Hermes virtual environment." }
    $oldProjectEnvironment = $env:UV_PROJECT_ENVIRONMENT
    $oldLinkMode = $env:UV_LINK_MODE
    try {
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $Destination "venv"
        $env:UV_LINK_MODE = "copy"
        Push-Location $Destination
        & $uvExe sync --frozen --no-dev --extra cron --extra feishu --python 3.11
        if ($LASTEXITCODE -ne 0) { throw "Locked Hermes dependencies could not be installed." }
    } finally {
        Pop-Location
        $env:UV_PROJECT_ENVIRONMENT = $oldProjectEnvironment
        $env:UV_LINK_MODE = $oldLinkMode
    }
}

function Install-CcSwitch([string]$PayloadDir) {
    $archive = Get-ChildItem -LiteralPath $PayloadDir -Filter "CC-Switch-*-Windows-Portable.zip" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $archive) {
        Write-Warning "CC Switch portable payload is not present. The tracker does not require it at runtime."
        return $null
    }
    Write-Step "Installing bundled CC Switch"
    $destination = Join-Path $env:LOCALAPPDATA "CCSwitchPortable"
    $existing = Get-ChildItem -LiteralPath $destination -Recurse -Filter "CC-Switch.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existing) { return $existing.FullName }
    if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
    Expand-Archive -LiteralPath $archive.FullName -DestinationPath $destination -Force
    $executable = Get-ChildItem -LiteralPath $destination -Recurse -Filter "CC-Switch.exe" -File | Select-Object -First 1
    if (-not $executable) { throw "CC Switch archive did not contain CC-Switch.exe" }
    $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\CC Switch.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($startMenu)
    $shortcut.TargetPath = $executable.FullName
    $shortcut.WorkingDirectory = $executable.DirectoryName
    $shortcut.Save()
    return $executable.FullName
}

function Test-DesktopApp([string]$Name, [string[]]$Candidates, [string]$DownloadUrl) {
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            Write-Host "[OK] $Name detected"
            return
        }
    }
    Write-Warning "$Name is not installed. Install it separately when needed: $DownloadUrl"
}

function Test-PayloadIntegrity([string]$PayloadDir) {
    $manifestPath = Join-Path $PayloadDir "PAYLOAD_MANIFEST.json"
    if (-not (Test-Path -LiteralPath $manifestPath)) { return }
    Write-Step "Verifying bundled open-source components"
    $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
    foreach ($asset in $manifest.assets) {
        $path = Join-Path $PayloadDir $asset.name
        if (-not (Test-Path -LiteralPath $path)) { throw "Bundled component is missing: $($asset.name)" }
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $asset.sha256.ToLowerInvariant()) {
            throw "Bundled component failed SHA-256 verification: $($asset.name)"
        }
    }
}

if ($DryRun) {
    Write-Host "Dry run configuration:"
    [pscustomobject]@{
        InstallDir = $InstallDir
        HermesHome = $HermesHome
        Schedule = $Schedule
        HermesTag = $HermesTag
    } | Format-List
    exit 0
}

if ([Environment]::OSVersion.Version.Major -lt 10) {
    throw "Windows 10 or Windows 11 is required."
}

$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$HermesHome = [IO.Path]::GetFullPath($HermesHome)
$env:HERMES_HOME = $HermesHome
Assert-FreeSpace -Path $HermesHome -MinimumBytes 5GB
$payloadDir = Join-Path $sourceRoot "payload"
Test-PayloadIntegrity -PayloadDir $payloadDir

Write-Step "Checking separately installed desktop applications"
Test-DesktopApp -Name "Feishu" -Candidates @(
    (Join-Path $env:LOCALAPPDATA "Feishu\Feishu.exe"),
    (Join-Path $env:LOCALAPPDATA "Lark\Lark.exe")
) -DownloadUrl "https://www.feishu.cn/download"
Test-DesktopApp -Name "Obsidian" -Candidates @(
    (Join-Path $env:LOCALAPPDATA "Obsidian\Obsidian.exe")
) -DownloadUrl "https://obsidian.md/download"

Write-Step "Installing tracker files"
Copy-Project -Source $sourceRoot -Destination $InstallDir
foreach ($folder in @("data", "raw", "reports", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir $folder) | Out-Null
}

$hermesInstallDir = Join-Path $HermesHome "hermes-agent"
$hermesExe = Join-Path $hermesInstallDir "venv\Scripts\hermes.exe"
$pythonExe = Join-Path $hermesInstallDir "venv\Scripts\python.exe"

if (-not $SkipHermesInstall -and -not (Test-Path -LiteralPath $hermesExe)) {
    Install-MinimalHermes `
        -HermesRoot $HermesHome `
        -Destination $hermesInstallDir `
        -Tag $HermesTag `
        -PinnedUvVersion $UvVersion `
        -PayloadDir $payloadDir
}

if (-not (Test-Path -LiteralPath $hermesExe) -or -not (Test-Path -LiteralPath $pythonExe)) {
    throw "Hermes installation is incomplete under $hermesInstallDir"
}

$ccSwitchExe = if ($SkipCcSwitchInstall) { $null } else { Install-CcSwitch -PayloadDir $payloadDir }

Write-Step "Configuring DeepSeek V4 Pro"
$envFile = Join-Path $HermesHome ".env"
$deepseekKey = Get-DotEnvValue -Path $envFile -Name "DEEPSEEK_API_KEY"
if (-not $deepseekKey -and $env:DEEPSEEK_API_KEY) {
    $deepseekKey = $env:DEEPSEEK_API_KEY
    Set-DotEnvValue -Path $envFile -Name "DEEPSEEK_API_KEY" -Value $deepseekKey
}
if (-not $deepseekKey) {
    $deepseekKey = Read-Secret "Paste the DeepSeek API key"
    if (-not $deepseekKey) { throw "A DeepSeek API key is required." }
    Set-DotEnvValue -Path $envFile -Name "DEEPSEEK_API_KEY" -Value $deepseekKey
}
Set-DotEnvValue -Path $envFile -Name "DEEPSEEK_BASE_URL" -Value "https://api.deepseek.com/v1"
& $hermesExe config set model.provider deepseek
& $hermesExe config set model.default deepseek-v4-pro
& $hermesExe config set model.base_url https://api.deepseek.com/v1

if (-not $SkipFeishuSetup) {
    $feishuId = Get-DotEnvValue -Path $envFile -Name "FEISHU_APP_ID"
    $feishuSecret = Get-DotEnvValue -Path $envFile -Name "FEISHU_APP_SECRET"
    if (-not $feishuId -or -not $feishuSecret) {
        Write-Step "Opening the Hermes messaging setup wizard"
        Write-Host "Choose Feishu/Lark in the wizard. QR setup is recommended."
        & $hermesExe gateway setup
        if ($LASTEXITCODE -ne 0) { throw "Hermes gateway setup did not complete." }
    }
}

if (-not $SkipGatewayService) {
    Write-Step "Installing and starting Hermes gateway"
    & $hermesExe gateway install --force
    if ($LASTEXITCODE -ne 0) { Write-Warning "Hermes gateway service installation returned $LASTEXITCODE." }
    & $hermesExe gateway start --all
    if ($LASTEXITCODE -ne 0) { throw "Hermes gateway could not start." }
    Add-WatchdogTask -ProjectDir $InstallDir -Home $HermesHome -HermesExe $hermesExe
}

$state = [ordered]@{
    schema = 1
    install_dir = $InstallDir
    hermes_home = $HermesHome
    hermes_tag = $HermesTag
    schedule = $Schedule
    installed_at = [DateTime]::UtcNow.ToString("o")
    cc_switch_exe = $ccSwitchExe
}
$stateJson = $state | ConvertTo-Json
$statePaths = @(
    (Join-Path $InstallDir ".install-state.json"),
    (Join-Path $sourceRoot ".install-state.json")
) | Select-Object -Unique
foreach ($statePath in $statePaths) {
    try {
        [IO.File]::WriteAllText($statePath, $stateJson, [Text.UTF8Encoding]::new($false))
    } catch {
        Write-Warning "Could not write installer state to $statePath"
    }
}

Write-Step "Initializing the tracker database"
$env:PYTHONPATH = Join-Path $InstallDir "src"
Push-Location $InstallDir
try {
    & $pythonExe -m medlit_tracker status | Out-Null

    if (-not $SkipFeishuSetup -and -not $Deliver) {
        Write-Host "Send any private message to the new Feishu bot now. Waiting up to 10 minutes..."
        $deadline = (Get-Date).AddMinutes(10)
        $targetReady = $false
        while ((Get-Date) -lt $deadline) {
            & $pythonExe (Join-Path $InstallDir "hermes\portable.py") target --json *> $null
            if ($LASTEXITCODE -eq 0) { $targetReady = $true; break }
            Start-Sleep -Seconds 5
        }
        if (-not $targetReady) {
            throw "No Feishu conversation was discovered. Send the bot a message and run INSTALL_WINDOWS.cmd again."
        }
    }

    Write-Step "Creating the Hermes-only daily tracking job"
    $installArguments = @(
        (Join-Path $InstallDir "hermes\portable.py"),
        "install",
        "--schedule",
        $Schedule
    )
    if ($Deliver) { $installArguments += @("--deliver", $Deliver) }
    & $pythonExe @installArguments
    if ($LASTEXITCODE -ne 0) { throw "Tracker job installation failed." }

    if (-not $SkipEndToEndTest) {
        Write-Step "Running the first end-to-end Hermes delivery test"
        & $pythonExe (Join-Path $InstallDir "hermes\portable.py") test --timeout 900
        if ($LASTEXITCODE -ne 0) { throw "End-to-end test failed." }
    }
} finally {
    Pop-Location
}

Write-Step "Installation complete"
Write-Host "Project: $InstallDir"
Write-Host "Hermes:  $HermesHome"
Write-Host "Schedule: $Schedule"
Write-Host "Run CHECK_WINDOWS.cmd at any time to verify the installation."
exit 0
