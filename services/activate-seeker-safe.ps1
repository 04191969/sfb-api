$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'seeker-port.ps1')
$source=Join-Path $PSScriptRoot 'seeker'
$base=Join-Path $env:USERPROFILE 'SFB\seeker'
$manifest=Get-Content (Join-Path $source 'release.json') -Raw | ConvertFrom-Json
$dest=Join-Path (Join-Path $base 'releases') $manifest.release_id
$python=Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
if (!(Test-Path $python)) {$python='python.exe'}
Write-Host 'Verifying Seeker release...'
$env:SFB_ACTIVATE_SOURCE=$source
& $python -c "import os,sys; sys.path.insert(0,os.environ['SFB_ACTIVATE_SOURCE']); import runtime; runtime.load_release(); print('Release integrity verified.',flush=True)"
if ($LASTEXITCODE -ne 0) {throw 'Verification failed. Nothing stopped.'}
if (Test-Path $dest) {
 $env:SFB_ACTIVATE_SOURCE=$dest
 & $python -c "import os,sys; sys.path.insert(0,os.environ['SFB_ACTIVATE_SOURCE']); import runtime; runtime.load_release()"
 if ($LASTEXITCODE -ne 0) {throw 'Existing release folder failed integrity verification. Nothing stopped.'}
 $existing=Get-Content (Join-Path $dest 'release.json') -Raw | ConvertFrom-Json
 if (($existing | ConvertTo-Json -Depth 10 -Compress) -ne ($manifest | ConvertTo-Json -Depth 10 -Compress)) {throw 'Existing release differs. Nothing stopped.'}
}
Write-Host 'Waiting up to 30 seconds for Seeker to finish its current job...'
New-Item -ItemType File -Force (Join-Path $base 'state\stop') | Out-Null
$deadline=(Get-Date).AddSeconds(30)
while (Test-SeekerBusy) {
 if ((Get-Date) -ge $deadline) {throw 'Seeker is still finishing. Nothing replaced. Wait for the current job to finish, then retry.'}
 Start-Sleep -Milliseconds 500
}
if (!(Test-Path $dest)) {Copy-Item $source $dest -Recurse}
# Update only Seeker's installed launcher, leaving the other component alone.
Copy-Item (Join-Path $PSScriptRoot 'seeker-port.ps1') (Join-Path $base 'seeker-port.ps1') -Force
Copy-Item (Join-Path $PSScriptRoot 'start-seeker-safe.ps1') (Join-Path $base 'start.ps1') -Force
Set-Content (Join-Path $base 'active.txt') $manifest.release_id
& (Join-Path $base 'start.ps1') -Component seeker
