$ErrorActionPreference='Stop'
$python=Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
if (-not (Test-Path $python)) { $python='python.exe' }
$out=Join-Path $env:USERPROFILE ('SFB_Engine_Captures_'+(Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
Write-Host '20 public engine routes, one broad query each. No retries, model calls, database writes, or worker changes.'
& $python -u -X utf8 (Join-Path $PSScriptRoot 'engine_capture_matrix.py') --output $out
Write-Host ('Report folder: '+$out)
