# =============================================================================
# deploy.ps1 — Deploy sealine-api to a remote Windows Server via WinRM
#
# Prerequisites (on YOUR machine):
#   - PowerShell 5.1+
#   - WinRM access to the remote server (run once: Enable-PSRemoting on remote)
#
# Usage:
#   .\deploy.ps1 -RemoteServer "myserver.domain.com" -RemoteUser "Administrator"
#   .\deploy.ps1 -RemoteServer "192.168.1.50"   # prompts for credentials
# =============================================================================

param(
    [Parameter(Mandatory=$true)]
    [string]$RemoteServer,

    [string]$RemoteUser = "Administrator",

    [string]$RemotePath = "C:\Apps\sealine-api",

    [int]$Port = 8001,

    # Path to Gmail credentials on YOUR local machine
    [string]$GmailCredentialsPath = "C:\Users\hangs\OneDrive\GitHub\OpenExxon\credentials.json",
    [string]$GmailTokenPath       = "C:\Users\hangs\OneDrive\GitHub\OpenExxon\token.json"
)

$ErrorActionPreference = "Stop"
$LocalRoot = $PSScriptRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  sealine-api Deployment" -ForegroundColor Cyan
Write-Host "  Target : $RemoteServer" -ForegroundColor Cyan
Write-Host "  Path   : $RemotePath" -ForegroundColor Cyan
Write-Host "  Port   : $Port" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Credentials ────────────────────────────────────────────────────────────
$cred = Get-Credential -UserName $RemoteUser -Message "Enter password for $RemoteServer"

# ── 2. Open PSSession ─────────────────────────────────────────────────────────
Write-Host "[1/6] Connecting to $RemoteServer ..." -ForegroundColor Yellow
$session = New-PSSession -ComputerName $RemoteServer -Credential $cred
Write-Host "      Connected." -ForegroundColor Green

# ── 3. Create remote directory structure ──────────────────────────────────────
Write-Host "[2/6] Creating remote directories ..." -ForegroundColor Yellow
Invoke-Command -Session $session -ScriptBlock {
    param($path)
    $dirs = @($path, "$path\static", "$path\memory")
    foreach ($d in $dirs) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }
    Write-Host "      Directories ready: $path"
} -ArgumentList $RemotePath

# ── 4. Copy application files ─────────────────────────────────────────────────
Write-Host "[3/6] Copying files ..." -ForegroundColor Yellow

# Core Python files
$filesToCopy = @("agent.py", "api.py", "requirements.txt", ".env")
foreach ($f in $filesToCopy) {
    $src = Join-Path $LocalRoot $f
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination "$RemotePath\$f" -ToSession $session -Force
        Write-Host "      Copied: $f"
    } else {
        Write-Warning "      Skipped (not found): $f"
    }
}

# Static folder
Copy-Item -Path "$LocalRoot\static\*" -Destination "$RemotePath\static\" -ToSession $session -Force -Recurse
Write-Host "      Copied: static/"

# Memory folder
Copy-Item -Path "$LocalRoot\memory\*" -Destination "$RemotePath\memory\" -ToSession $session -Force -Recurse
Write-Host "      Copied: memory/"

# Gmail credentials (if they exist)
if (Test-Path $GmailCredentialsPath) {
    Copy-Item -Path $GmailCredentialsPath -Destination "$RemotePath\credentials.json" -ToSession $session -Force
    Write-Host "      Copied: credentials.json"
} else {
    Write-Warning "      Gmail credentials not found at: $GmailCredentialsPath"
}
if (Test-Path $GmailTokenPath) {
    Copy-Item -Path $GmailTokenPath -Destination "$RemotePath\token.json" -ToSession $session -Force
    Write-Host "      Copied: token.json"
} else {
    Write-Warning "      Gmail token not found at: $GmailTokenPath"
}

# ── 5. Install Python dependencies ────────────────────────────────────────────
Write-Host "[4/6] Installing Python dependencies ..." -ForegroundColor Yellow
Invoke-Command -Session $session -ScriptBlock {
    param($path)
    Set-Location $path
    $result = & python -m pip install -r requirements.txt --quiet 2>&1
    if ($LASTEXITCODE -ne 0) { throw "pip install failed: $result" }
    Write-Host "      Dependencies installed."
} -ArgumentList $RemotePath

# ── 6. Install / update NSSM service ─────────────────────────────────────────
Write-Host "[5/6] Configuring NSSM service ..." -ForegroundColor Yellow
Invoke-Command -Session $session -ScriptBlock {
    param($path, $port)

    $serviceName = "sealine-api"
    $python      = (Get-Command python).Source
    $nssmPath    = "C:\nssm\nssm.exe"

    # Download NSSM if not present
    if (-not (Test-Path $nssmPath)) {
        Write-Host "      Downloading NSSM ..."
        $nssmDir = "C:\nssm"
        New-Item -ItemType Directory -Path $nssmDir -Force | Out-Null
        $zipUrl  = "https://nssm.cc/release/nssm-2.24.zip"
        $zipFile = "$env:TEMP\nssm.zip"
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipFile -UseBasicParsing
        Expand-Archive -Path $zipFile -DestinationPath "$env:TEMP\nssm_extracted" -Force
        Copy-Item "$env:TEMP\nssm_extracted\nssm-2.24\win64\nssm.exe" $nssmPath
        Remove-Item $zipFile -Force
    }

    # Remove existing service if present
    $svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Host "      Stopping existing service ..."
        & $nssmPath stop  $serviceName confirm 2>$null
        & $nssmPath remove $serviceName confirm 2>$null
    }

    # Install service
    & $nssmPath install $serviceName $python "-m uvicorn api:app --host 0.0.0.0 --port $port"
    & $nssmPath set     $serviceName AppDirectory    $path
    & $nssmPath set     $serviceName AppStdout       "$path\logs\stdout.log"
    & $nssmPath set     $serviceName AppStderr       "$path\logs\stderr.log"
    & $nssmPath set     $serviceName AppRotateFiles  1
    & $nssmPath set     $serviceName Start           SERVICE_AUTO_START
    & $nssmPath set     $serviceName DisplayName     "Sealine API"
    & $nssmPath set     $serviceName Description     "Sealine shipping database FastAPI + Claude agent"

    # Create logs folder
    New-Item -ItemType Directory -Path "$path\logs" -Force | Out-Null

    Write-Host "      NSSM service '$serviceName' installed."
} -ArgumentList $RemotePath, $Port

# ── 7. Start service ──────────────────────────────────────────────────────────
Write-Host "[6/6] Starting sealine-api service ..." -ForegroundColor Yellow
Invoke-Command -Session $session -ScriptBlock {
    $nssmPath = "C:\nssm\nssm.exe"
    & $nssmPath start "sealine-api"
    Start-Sleep -Seconds 3
    $svc = Get-Service -Name "sealine-api"
    Write-Host "      Service status: $($svc.Status)"
}

# ── Done ──────────────────────────────────────────────────────────────────────
Remove-PSSession $session

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deployment complete!" -ForegroundColor Green
Write-Host "  API: http://$RemoteServer`:$Port" -ForegroundColor Green
Write-Host "  UI : http://$RemoteServer`:$Port/static/index.html" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Service management (on remote server):" -ForegroundColor Cyan
Write-Host "  Start : nssm start sealine-api"
Write-Host "  Stop  : nssm stop  sealine-api"
Write-Host "  Status: Get-Service sealine-api"
Write-Host "  Logs  : $RemotePath\logs\"
