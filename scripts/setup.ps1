param([switch]$SkipGpu)
. "$PSScriptRoot/common.ps1"
if (-not (Test-Path -LiteralPath $ProjectPython)) { python -m venv .venv; Check-Exit }
& $ProjectPython -m pip install -r requirements.lock; Check-Exit
& $ProjectPython -m pip install --no-deps -e .; Check-Exit
if (-not $SkipGpu) {
    & $ProjectPython -m pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cu128 --no-cache-dir; Check-Exit
}
Push-Location frontend
try { npm ci --no-audit --no-fund; Check-Exit; npm run build; Check-Exit } finally { Pop-Location }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
Write-Host '安装完成。运行 scripts/start.ps1 打开演示；真实模式还需要模型和 API 配置。'
