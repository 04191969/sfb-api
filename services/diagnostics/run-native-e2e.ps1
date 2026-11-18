$ErrorActionPreference = 'Stop'
$repo = Join-Path $env:USERPROFILE 'Documents\cursor\sfb-api'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$root = Join-Path $env:USERPROFILE "SFB_Native_Test_$stamp"
New-Item -ItemType Directory -Path $root | Out-Null
$archive = Join-Path $root 'code.zip'
# Export only: no checkout, installed release, process, or task is changed.
& git -C $repo archive origin/review/native-seeker-v2 --format=zip "--output=$archive"
if ($LASTEXITCODE -ne 0) {throw 'Could not export test code. Nothing activated.'}
$code = Join-Path $root 'code'
Expand-Archive -Path $archive -DestinationPath $code
$python = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
if (-not (Test-Path $python)) {$python = 'python.exe'}
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$previousKey = $env:OPENAI_KEY
try {
 if (-not $env:OPENAI_KEY -and -not $env:OPENAI_API_KEY) {
  $env:OPENAI_KEY = [Environment]::GetEnvironmentVariable('OPENAI_KEY','User')
  if (-not $env:OPENAI_KEY) {$env:OPENAI_KEY = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY','User')}
 }
 Write-Host 'Isolated LIVE Seeker test: maximum 17 paid model calls, no retries. Workers and database unchanged.'
 & $python (Join-Path $code 'services\diagnostics\native_end_to_end.py') --output (Join-Path $root 'results')
 if ($LASTEXITCODE -ne 0) {throw 'Test stopped. Keep this test folder.'}
} finally {$env:OPENAI_KEY = $previousKey}
