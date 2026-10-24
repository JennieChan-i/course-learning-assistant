param([ValidateSet('sample','private')][string]$Dataset='sample', [ValidateSet('dev','test')][string]$Split='dev', [string]$Embedding='BAAI/bge-small-zh-v1.5')
. "$PSScriptRoot/common.ps1"
& $ProjectPython -m backend.cli eval --dataset $Dataset --split $Split --embedding $Embedding; Check-Exit
