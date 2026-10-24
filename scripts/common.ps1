$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $ProjectRoot
$ProjectPython = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
New-Item -ItemType Directory -Force (Join-Path $ProjectRoot '.cache/tmp') | Out-Null
$env:TEMP = Join-Path $ProjectRoot '.cache/tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $ProjectRoot '.cache/pip'
$env:npm_config_cache = Join-Path $ProjectRoot '.cache/npm'
$env:HF_HOME = Join-Path $ProjectRoot '.cache/huggingface'
$env:HF_HUB_DISABLE_XET = '1'
$env:PYTHONUTF8 = '1'
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "上一条命令失败，退出码：$LASTEXITCODE" } }
