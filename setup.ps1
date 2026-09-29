$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$uvDir = Join-Path $PSScriptRoot ".tools\uv"
$uvBin = Join-Path $uvDir "uv.exe"
$venvDir = Join-Path $PSScriptRoot ".venv"
$pythonBin = Join-Path $venvDir "Scripts\python.exe"
$bootstrapMarker = Join-Path $venvDir ".wa-bot-bootstrap-hash"

try {
    if (-not (Test-Path $uvBin)) {
        Write-Host "==> Скачиваем менеджер окружения uv"
        New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
        $env:UV_UNMANAGED_INSTALL = $uvDir
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }

    if ($env:WA_BOT_FORCE_REPAIR -eq "1") {
        Write-Host "==> Пересоздаём окружение Python"
        & $uvBin venv --clear --python 3.12 $venvDir
        if ($LASTEXITCODE -ne 0) { throw "Не удалось пересоздать Python" }
    } elseif (-not (Test-Path $pythonBin)) {
        Write-Host "==> Устанавливаем управляемый Python 3.12"
        & $uvBin venv --python 3.12 $venvDir
        if ($LASTEXITCODE -ne 0) { throw "Не удалось установить Python" }
    }

    $pythonHealthy = $true
    try {
        & $pythonBin --version *> $null
        if ($LASTEXITCODE -ne 0) { $pythonHealthy = $false }
    } catch {
        $pythonHealthy = $false
    }
    if (-not $pythonHealthy) {
        Write-Host "==> Окружение Python повреждено, пересоздаём"
        & $uvBin venv --clear --python 3.12 $venvDir
        if ($LASTEXITCODE -ne 0) { throw "Не удалось восстановить Python" }
    }

    $requirementsHash = (Get-FileHash -Algorithm SHA256 (Join-Path $PSScriptRoot "requirements.txt")).Hash.ToLowerInvariant()
    $environmentReady = $false
    & $pythonBin -c 'from pathlib import Path; import fastapi, uvicorn; from playwright.sync_api import sync_playwright; p = sync_playwright().start(); path = p.firefox.executable_path; p.stop(); raise SystemExit(0 if Path(path).is_file() else 1)' *> $null
    if ($LASTEXITCODE -eq 0) { $environmentReady = $true }
    if ($env:WA_BOT_FORCE_REPAIR -ne "1" -and $environmentReady -and (Test-Path $bootstrapMarker)) {
        $installedHash = (Get-Content -Raw $bootstrapMarker).Trim()
        if ($installedHash -eq $requirementsHash) {
            Write-Host "==> Окружение уже готово"
            return
        }
    }

    Write-Host "==> Проверяем зависимости приложения"
    & $uvBin --system-certs pip install --python $pythonBin -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости" }

    Write-Host "==> Проверяем Firefox Playwright"
    & $pythonBin -m playwright install firefox
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить Firefox Playwright" }

    Set-Content -NoNewline -Path $bootstrapMarker -Value $requirementsHash
    Write-Host "==> Окружение готово"
} catch {
    Write-Host ""
    throw "Подготовка окружения завершилась с ошибкой: $_"
}
