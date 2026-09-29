$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:WA_BOT_FORCE_REPAIR = "1"
& (Join-Path $PSScriptRoot "setup.ps1")
exit $LASTEXITCODE
