# =============================================================================
# remote_setup.ps1 — Run ONCE on the remote Windows Server to install
#                    all prerequisites for sealine-api.
#
# Run as Administrator:
#   PowerShell -ExecutionPolicy Bypass -File remote_setup.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  sealine-api Remote Server Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── Helper ────────────────────────────────────────────────────────────────────
function Install-IfMissing($name, $testCmd, $installBlock) {
    Write-Host "Checking $name ..." -ForegroundColor Yellow -NoNewline
    try {
        & $testCmd | Out-Null
        Write-Host " already installed." -ForegroundColor Green
    } catch {
        Write-Host " not found. Installing ..." -ForegroundColor Red
        & $installBlock
        Write-Host "  $name installed." -ForegroundColor Green
    }
}

# ── 1. Enable WinRM (so deploy.ps1 can connect) ───────────────────────────────
Write-Host "[1/5] Enabling WinRM ..." -ForegroundColor Yellow
Enable-PSRemoting -Force -SkipNetworkProfileCheck | Out-Null
Set-Item WSMan:\localhost\Client\TrustedHosts -Value "*" -Force
Write-Host "      WinRM enabled." -ForegroundColor Green

# ── 2. Python 3 ───────────────────────────────────────────────────────────────
Write-Host "[2/5] Checking Python ..." -ForegroundColor Yellow
$pythonOk = $false
try { $v = python --version 2>&1; Write-Host "      Found: $v" -ForegroundColor Green; $pythonOk = $true } catch {}

if (-not $pythonOk) {
    Write-Host "      Downloading Python 3.12 ..." -ForegroundColor Yellow
    $pyUrl    = "https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe"
    $pyInst   = "$env:TEMP\python-installer.exe"
    Invoke-WebRequest -Uri $pyUrl -OutFile $pyInst -UseBasicParsing
    Start-Process -FilePath $pyInst -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1" -Wait
    Remove-Item $pyInst -Force
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
    Write-Host "      Python installed." -ForegroundColor Green
}

# ── 3. ODBC Driver 17 for SQL Server ──────────────────────────────────────────
Write-Host "[3/5] Checking ODBC Driver 17 for SQL Server ..." -ForegroundColor Yellow
$odbcKey = "HKLM:\SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 17 for SQL Server"
if (Test-Path $odbcKey) {
    Write-Host "      Already installed." -ForegroundColor Green
} else {
    Write-Host "      Downloading ODBC Driver 17 ..." -ForegroundColor Yellow
    $odbcUrl  = "https://go.microsoft.com/fwlink/?linkid=2120137"
    $odbcInst = "$env:TEMP\msodbcsql17.msi"
    Invoke-WebRequest -Uri $odbcUrl -OutFile $odbcInst -UseBasicParsing
    Start-Process msiexec.exe -ArgumentList "/i `"$odbcInst`" /quiet IACCEPTMSODBCSQLLICENSETERMS=YES" -Wait
    Remove-Item $odbcInst -Force
    Write-Host "      ODBC Driver 17 installed." -ForegroundColor Green
}

# ── 4. NSSM ───────────────────────────────────────────────────────────────────
Write-Host "[4/5] Checking NSSM ..." -ForegroundColor Yellow
$nssmPath = "C:\nssm\nssm.exe"
if (Test-Path $nssmPath) {
    Write-Host "      Already present at $nssmPath." -ForegroundColor Green
} else {
    Write-Host "      Downloading NSSM ..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path "C:\nssm" -Force | Out-Null
    $zipUrl  = "https://nssm.cc/release/nssm-2.24.zip"
    $zipFile = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipFile -UseBasicParsing
    Expand-Archive -Path $zipFile -DestinationPath "$env:TEMP\nssm_extracted" -Force
    Copy-Item "$env:TEMP\nssm_extracted\nssm-2.24\win64\nssm.exe" $nssmPath
    Remove-Item $zipFile -Force
    # Add C:\nssm to system PATH
    $sysPath = [System.Environment]::GetEnvironmentVariable("Path","Machine")
    if ($sysPath -notlike "*C:\nssm*") {
        [System.Environment]::SetEnvironmentVariable("Path", "$sysPath;C:\nssm", "Machine")
    }
    Write-Host "      NSSM installed at $nssmPath." -ForegroundColor Green
}

# ── 5. Open firewall for port 8001 ────────────────────────────────────────────
Write-Host "[5/5] Configuring firewall for port 8001 ..." -ForegroundColor Yellow
$ruleName = "sealine-api-8001"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "      Firewall rule already exists." -ForegroundColor Green
} else {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction   Inbound `
        -Protocol    TCP `
        -LocalPort   8001 `
        -Action      Allow `
        -Profile     Any | Out-Null
    Write-Host "      Firewall rule created (TCP 8001 inbound)." -ForegroundColor Green
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Prerequisites ready!" -ForegroundColor Green
Write-Host "  Now run deploy.ps1 from your local" -ForegroundColor Green
Write-Host "  machine to push the application." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
