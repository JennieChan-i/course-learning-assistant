param(
    [Parameter(Mandatory=$true)][string]$Cases,
    [ValidateSet('sample','private')][string]$Dataset = 'sample',
    [ValidateRange(1,20)][int]$MaxCases = 1
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    & "$projectRoot/.venv/Scripts/python.exe" -m backend.cli badcase-eval --dataset $Dataset --questions $Cases --max-cases $MaxCases
    if ($LASTEXITCODE -ne 0) { throw 'Badcase regression execution failed.' }
} finally { Pop-Location }
