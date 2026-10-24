. "$PSScriptRoot/common.ps1"
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) {
    Push-Location frontend
    try { npm run build; Check-Exit } finally { Pop-Location }
}
Write-Host '工作台默认地址 http://127.0.0.1:8765 （可在 .env 设置 APP_PORT）'
& $ProjectPython -m backend.cli serve
