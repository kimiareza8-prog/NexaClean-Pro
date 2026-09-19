$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot
$Log = Join-Path $PSScriptRoot 'setup.log'
Start-Transcript -Path $Log -Append | Out-Null
function Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Find-Python {
    $Candidates = @()
    try { $P = Get-Command py.exe -ErrorAction Stop; $Candidates += ,@($P.Source, '-3') } catch {}
    try { $P = Get-Command python.exe -ErrorAction Stop; $Candidates += ,@($P.Source) } catch {}
    foreach ($P in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python313\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )) { if ($P -and (Test-Path -LiteralPath $P)) { $Candidates += ,@($P) } }
    foreach ($Candidate in $Candidates) {
        try {
            $Exe = $Candidate[0]; $Extra = @()
            if ($Candidate.Count -gt 1) { $Extra = $Candidate[1..($Candidate.Count - 1)] }
            $V = & $Exe @Extra -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $V) {
                $Parts = $V.Trim().Split('.')
                if ([int]$Parts[0] -ge 3 -and [int]$Parts[1] -ge 10) { return $Candidate }
            }
        } catch {}
    }
    return $null
}
try {
    Step 'Preparing NexaClean Pro'
    $PythonCmd = Find-Python
    if (-not $PythonCmd) {
        Step 'Installing Python 3.12'
        $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $Winget) {
            Start-Process 'https://www.python.org/downloads/windows/'
            throw 'Python 3.10+ is required. The Python download page was opened.'
        }
        & $Winget.Source install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
        Start-Sleep -Seconds 2
        $PythonCmd = Find-Python
        if (-not $PythonCmd) { throw 'Python was installed. Restart Windows and run START again.' }
    }
    $PythonExe = $PythonCmd[0]; $PythonExtra = @()
    if ($PythonCmd.Count -gt 1) { $PythonExtra = $PythonCmd[1..($PythonCmd.Count - 1)] }
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        Step 'Creating private environment'
        & $PythonExe @PythonExtra -m venv '.venv'
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the application environment.' }
    }
    $VenvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    Step 'Installing required components'
    & $VenvPython -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw 'pip update failed.' }
    & $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    Step 'Checking application'
    & $VenvPython -m py_compile (Join-Path $PSScriptRoot 'NexaClean_Pro.py')
    if ($LASTEXITCODE -ne 0) { throw 'Application check failed.' }
    try {
        $Desktop = [Environment]::GetFolderPath('Desktop')
        $WS = New-Object -ComObject WScript.Shell
        $Shortcut = $WS.CreateShortcut((Join-Path $Desktop 'NexaClean Pro.lnk'))
        $Starter = Join-Path (Split-Path $PSScriptRoot -Parent) 'NexaClean-Pro-START.bat'
        $Shortcut.TargetPath = $Starter
        $Shortcut.WorkingDirectory = (Split-Path $PSScriptRoot -Parent)
        $Shortcut.Description = 'NexaClean Pro 3.5'
        $Shortcut.Save()
    } catch {}
    Write-Host "`nReady. Launching NexaClean Pro..." -ForegroundColor Green
    Stop-Transcript | Out-Null
    Start-Process -FilePath $VenvPython -ArgumentList @((Join-Path $PSScriptRoot 'NexaClean_Pro.py')) -WorkingDirectory $PSScriptRoot
    exit 0
} catch {
    Write-Host "`nNexaClean setup error: $($_.Exception.Message)" -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host 'Press Enter to close'
    exit 1
}
