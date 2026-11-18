$ErrorActionPreference = 'Stop'
$python = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
if (-not (Test-Path $python)) { $python = 'python.exe' }
$out = Join-Path $env:USERPROFILE ('SFB_Buyer_Browser_Proof_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
Write-Host 'Isolated steps 1 and 2 only. No model calls, database writes, worker restarts or production changes.'
Write-Host 'One Qwant search only. Captures the first failure and stops. No retries.'
& $python -u -X utf8 (Join-Path $PSScriptRoot 'browser_buyer_proof.py') --output $out --diagnose-first
Write-Host ('Report folder: ' + $out)
