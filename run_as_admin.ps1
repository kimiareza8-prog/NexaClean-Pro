$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonw = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$app = Join-Path $PSScriptRoot 'NexaClean_Pro.py'
if (-not (Test-Path -LiteralPath $app)) {
    Start-Process -FilePath (Join-Path $PSScriptRoot 'SETUP.bat') -WorkingDirectory $PSScriptRoot -Wait
}
if (-not (Test-Path -LiteralPath $pythonw)) {
    Start-Process -FilePath (Join-Path $PSScriptRoot 'SETUP.bat') -WorkingDirectory $PSScriptRoot -Wait
}
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw 'NexaClean environment is not installed. Run SETUP.bat first.'
}
Start-Process -FilePath $pythonw -ArgumentList @("`"$app`"") -WorkingDirectory $PSScriptRoot -Verb RunAs
