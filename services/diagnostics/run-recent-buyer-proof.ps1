$ErrorActionPreference='Stop'
$python=Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
if (-not (Test-Path $python)) { $python='python.exe' }
if (-not $env:OPENAI_KEY) { $env:OPENAI_KEY=[Environment]::GetEnvironmentVariable('OPENAI_KEY','User') }
if (-not $env:OPENAI_API_KEY) { $env:OPENAI_API_KEY=[Environment]::GetEnvironmentVariable('OPENAI_API_KEY','User') }
$out=Join-Path $env:USERPROFILE ('SFB_Recent_Buyer_Proof_'+(Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
Write-Host 'Broad discovery + existing Seeker reader. Up to 3 paid calls, only for dated posts within 90 days. No retries or worker changes.'
& $python -u -X utf8 (Join-Path $PSScriptRoot 'recent_buyer_proof.py') --output $out
