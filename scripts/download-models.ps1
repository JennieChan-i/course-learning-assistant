param([switch]$IncludeBase)
. "$PSScriptRoot/common.ps1"
$ModelArgs = @('-m', 'backend.cli', 'download')
if ($IncludeBase) { $ModelArgs += '--include-base' }
& $ProjectPython @ModelArgs; Check-Exit
