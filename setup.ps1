$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot

# GitHub/repository fallback: reconstruct the main Python source from the compact
# gzip+base64 payload when a freshly downloaded repository has not materialized it yet.
$MainPy = Join-Path $PSScriptRoot 'NexaClean_Pro.py'
$PackedSource = Join-Path $PSScriptRoot 'src\NexaClean_Pro.py.gz.b64'
if (-not (Test-Path -LiteralPath $MainPy) -and (Test-Path -LiteralPath $PackedSource)) {
    Write-Host '[NexaClean] Reconstructing NexaClean_Pro.py from packaged source...'
    $b64 = (Get-Content -Raw -LiteralPath $PackedSource).Trim()
    $compressed = [Convert]::FromBase64String($b64)
    $input = New-Object System.IO.MemoryStream(,$compressed)
    $gzip = New-Object System.IO.Compression.GzipStream($input, [IO.Compression.CompressionMode]::Decompress)
    $output = [System.IO.File]::Create($MainPy)
    try { $gzip.CopyTo($output) } finally { $output.Dispose(); $gzip.Dispose(); $input.Dispose() }
}

$Log = Join-Path $PSScriptRoot 'setup.log'
Start-Transcript -Path $Log -Append | Out-Null

function Write-Step([string]$Text) {
    Write-Host "`n==> $Text" -ForegroundColor Cyan
}

function Find-Python {
    $candidates = @()
    try {
        $py = Get-Command py.exe -ErrorAction Stop
        $candidates += ,@($py.Source, '-3')
    } catch {}
    try {
        $python = Get-Command python.exe -ErrorAction Stop
        $candidates += ,@($python.Source)
    } catch {}

    $common = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python313\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    foreach ($p in $common) {
        if ($p -and (Test-Path -LiteralPath $p)) { $candidates += ,@($p) }
    }

    foreach ($candidate in $candidates) {
        try {
            $exe = $candidate[0]
            $extra = @()
            if ($candidate.Count -gt 1) { $extra = $candidate[1..($candidate.Count-1)] }
            $ver = & $exe @extra -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver) {
                $parts = $ver.Trim().Split('.')
                if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                    return $candidate
                }
            }
        } catch {}
    }
    return $null
}

try {
    Write-Step 'Checking Python 3.10+'
    $pythonCmd = Find-Python

    if (-not $pythonCmd) {
        Write-Step 'Python was not found. Installing Python 3.12 with winget...'
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) {
            Write-Host 'winget is not available on this Windows installation.' -ForegroundColor Red
            Write-Host 'Install Python 3.10+ from python.org, then run SETUP.bat again.' -ForegroundColor Yellow
            Start-Process 'https://www.python.org/downloads/windows/'
            throw 'Python is missing and winget is unavailable.'
        }
        & $winget.Source install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw "winget Python install failed with exit code $LASTEXITCODE" }
        Start-Sleep -Seconds 2
        $pythonCmd = Find-Python
        if (-not $pythonCmd) { throw 'Python was installed but could not be located. Sign out/in or restart Windows, then run SETUP.bat again.' }
    }

    $pythonExe = $pythonCmd[0]
    $pythonExtra = @()
    if ($pythonCmd.Count -gt 1) { $pythonExtra = $pythonCmd[1..($pythonCmd.Count-1)] }

    Write-Step 'Creating isolated virtual environment (.venv)'
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & $pythonExe @pythonExtra -m venv '.venv'
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
    }

    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    Write-Step 'Updating pip'
    & $venvPython -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }

    Write-Step 'Installing NexaClean dependencies'
    & $venvPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

    Write-Step 'Verifying Python, Tkinter, Pillow, OpenCV, send2trash and psutil'
    & $venvPython -c "import tkinter, PIL, cv2, send2trash, psutil; print('Dependencies OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed.' }

    Write-Step 'Checking application syntax'
    & $venvPython -m py_compile (Join-Path $PSScriptRoot 'NexaClean_Pro.py')
    if ($LASTEXITCODE -ne 0) { throw 'Application syntax check failed.' }

    Write-Step 'Creating desktop shortcut'
    try {
        $desktop = [Environment]::GetFolderPath('Desktop')
        $ws = New-Object -ComObject WScript.Shell
        $shortcut = $ws.CreateShortcut((Join-Path $desktop 'NexaClean Pro.lnk'))
        $shortcut.TargetPath = (Join-Path $PSScriptRoot 'RUN.bat')
        $shortcut.WorkingDirectory = $PSScriptRoot
        $shortcut.Description = 'NexaClean Pro 3.4'
        $shortcut.Save()
    } catch {
        Write-Host "Desktop shortcut could not be created: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    Write-Host "`nSetup completed successfully." -ForegroundColor Green
    Write-Host 'You can now use RUN.bat. For force-delete operations, use RUN_AS_ADMIN.bat.' -ForegroundColor Green
    Write-Host 'Launching NexaClean Pro...' -ForegroundColor Green
    Stop-Transcript | Out-Null
    Start-Process -FilePath (Join-Path $PSScriptRoot 'RUN.bat') -WorkingDirectory $PSScriptRoot
    exit 0
}
catch {
    Write-Host "`nSETUP ERROR: $($_.Exception.Message)" -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
