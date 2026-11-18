$ErrorActionPreference='Stop'
$python=Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
if (-not (Test-Path $python)) { $python='python.exe' }
if (-not $env:OPENAI_KEY) { $env:OPENAI_KEY=[Environment]::GetEnvironmentVariable('OPENAI_KEY','User') }
if (-not $env:OPENAI_API_KEY) { $env:OPENAI_API_KEY=[Environment]::GetEnvironmentVariable('OPENAI_API_KEY','User') }
Write-Host 'Saved buyer text to the installed Seeker reader. Maximum ONE paid call. No worker restart, production writes, seller search or introduction.'
& $python -u -X utf8 (Join-Path $PSScriptRoot 'prove_reader_handoff.py')
