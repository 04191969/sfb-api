$ErrorActionPreference='Stop'
$python='C:\Users\drjul\AppData\Local\Python\pythoncore-3.14-64\python.exe'
if (!(Test-Path $python)) {$python='python.exe'}
$env:SUPABASE_KEY=[Environment]::GetEnvironmentVariable('SUPABASE_KEY','User')
if (!$env:SUPABASE_KEY) {throw 'Missing worker credential. Nothing stopped.'}
$package=[Environment]::GetEnvironmentVariable('SFB_LOCKED_SEEKER_DIR','User')
if (!$package) {$package=Join-Path $env:USERPROFILE 'Documents\test it\Seeker_v0_9_1_QQ2_handoff_fix\Seeker_locked_v0_9_1_plus_QQ2'}
$env:SFB_INSTALL_ROOT=$PSScriptRoot
$env:SFB_INSTALL_PACKAGE=$package
# Validate both releases and the existing pinned external Seeker package before stopping anything.
& $python (Join-Path $PSScriptRoot 'verify-install.py')
if ($LASTEXITCODE -ne 0) {throw 'Isolation preflight failed. Existing worker was not stopped.'}
$homeRoot=Join-Path $env:USERPROFILE 'SFB'
foreach ($component in @('live_matches','seeker')) {
 $source=Join-Path $PSScriptRoot $component
 $manifest=Get-Content (Join-Path $source 'release.json') -Raw | ConvertFrom-Json
 $base=Join-Path $homeRoot $component
 $dest=Join-Path (Join-Path $base 'releases') $manifest.release_id
 if (Test-Path $dest) {throw "Release directory already exists: $dest. Nothing stopped; inspect existing installation."}
 New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
 Copy-Item $source $dest -Recurse
 Copy-Item (Join-Path $PSScriptRoot 'start-component.ps1') (Join-Path $base 'start.ps1')
 Copy-Item (Join-Path $PSScriptRoot 'stop-component.ps1') (Join-Path $base 'stop.ps1')
 New-Item -ItemType Directory -Force (Join-Path $base 'state') | Out-Null
 Set-Content (Join-Path $base 'active.txt') $manifest.release_id
}
if (Test-Path (Join-Path $homeRoot 'seeker\package')) {throw 'Seeker package destination already exists; inspect it before cutover.'}
Copy-Item $package (Join-Path $homeRoot 'seeker\package') -Recurse
# Copy cooldown history independently. Never move or overwrite the original.
foreach ($component in @('live_matches','seeker')) {
 $seen=Join-Path $env:USERPROFILE 'sfb_seen_items.json'
 if (Test-Path $seen) {Copy-Item $seen (Join-Path $homeRoot "$component\state\seen.json")}
}
# Recheck the queue just before the one-time cutover. Do not interrupt a search.
& $python (Join-Path $PSScriptRoot 'verify-install.py')
if ($LASTEXITCODE -ne 0) {throw 'A job appeared during preparation. Existing worker left running; do not rerun installer blindly.'}
$old=Get-ScheduledTask -TaskName 'SFB Seeker' -ErrorAction SilentlyContinue
if ($old) {
 Export-ScheduledTask -TaskName 'SFB Seeker' | Set-Content (Join-Path $homeRoot 'legacy-seeker-task.xml')
 Disable-ScheduledTask -TaskName 'SFB Seeker' | Out-Null
}
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like '*SFB_Seeker_v2_env.bat*' } | ForEach-Object {Stop-Process -Id $_.ProcessId -ErrorAction Stop}
$enable=@'
import os
from supabase import create_client
sb=create_client('https://fyxglozykvhrfejrcrqz.supabase.co',os.environ['SUPABASE_KEY'])
for component in ('live_matches','seeker'):
 sb.rpc('set_sfb_component_enabled',{'component_name':component,'should_enable':True}).execute()
'@
$enable | & $python -
if ($LASTEXITCODE -ne 0) {throw 'Activation failed. Releases are installed; inspect database connection before starting.'}
foreach ($component in @('live_matches','seeker')) {
 $base=Join-Path $homeRoot $component
 $action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $base 'start.ps1')+'" -Component '+$component)
 $trigger=New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
 $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 Register-ScheduledTask -TaskName "SFB $component" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
 Start-ScheduledTask -TaskName "SFB $component"
}
Remove-Item Env:SUPABASE_KEY -ErrorAction SilentlyContinue
Write-Host 'Independent workers requested. Live Matches and Seeker have separate release folders, V2 copies, processes, state and logs. Verify both heartbeats before testing.'
