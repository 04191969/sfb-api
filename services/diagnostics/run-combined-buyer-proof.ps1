$ErrorActionPreference='Stop'
$python=Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
if (-not (Test-Path $python)) { $python='python.exe' }
$out=Join-Path $env:USERPROFILE ('SFB_Combined_Buyer_Proof_'+(Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
$state=Join-Path $env:USERPROFILE 'SFB_Combined_Buyer_Proof_State\state.json'
Write-Host 'Isolated broad searches and rotating WTB boards. Six searches, at most twelve source visits. No paid model calls or worker changes.'
& $python -u -X utf8 (Join-Path $PSScriptRoot 'combined_buyer_proof.py') --output $out --state $state
