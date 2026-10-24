param([ValidateSet('sample','private')][string]$Dataset='sample', [string]$Embedding='BAAI/bge-small-zh-v1.5')
. "$PSScriptRoot/common.ps1"
& $ProjectPython -m backend.cli index --dataset $Dataset --embedding $Embedding; Check-Exit
