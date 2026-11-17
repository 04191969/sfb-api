param([Parameter(Mandatory=$true)][ValidateSet('live_matches','seeker')][string]$Component)
$ErrorActionPreference='Stop'
$source=Join-Path $PSScriptRoot $Component
$manifest=Get-Content (Join-Path $source 'release.json') -Raw | ConvertFrom-Json
$base=Join-Path $env:USERPROFILE "SFB\$Component"
$dest=Join-Path (Join-Path $base 'releases') $manifest.release_id
if (Test-Path $dest) {throw 'This release already exists. No files or processes changed.'}
$python='C:\Users\drjul\AppData\Local\Python\pythoncore-3.14-64\python.exe'
if (!(Test-Path $python)) {$python='python.exe'}
$env:SFB_ACTIVATE_SOURCE=$source
$verify=@'
import os,sys
from pathlib import Path
sys.path.insert(0,os.environ['SFB_ACTIVATE_SOURCE'])
import runtime
runtime.load_release()
print('Release integrity verified.')
'@
$verify | & $python -
if ($LASTEXITCODE -ne 0) {throw 'Release verification failed. Nothing stopped.'}
$active=(Get-Content (Join-Path $base 'active.txt') -Raw).Trim()
$oldEntry=Join-Path (Join-Path (Join-Path $base 'releases') $active) 'runtime.py'
New-Item -ItemType File -Force (Join-Path $base 'state\stop') | Out-Null
$deadline=(Get-Date).AddSeconds(30)
do {
 $running=Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {$_.CommandLine -and $_.CommandLine.Contains($oldEntry)}
 if (!$running) {break}
 Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)
if ($running) {throw 'Current job is finishing. Release was not replaced. Wait for this component to stop, then run activation again.'}
Copy-Item $source $dest -Recurse
Set-Content (Join-Path $base 'active.txt') $manifest.release_id
& (Join-Path $base 'start.ps1') -Component $Component
Write-Host "$Component release activated. The other component was not stopped or updated."
