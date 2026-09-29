$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& (Join-Path $PSScriptRoot "setup.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$pythonBin = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $pythonBin main.py @args
exit $LASTEXITCODE
