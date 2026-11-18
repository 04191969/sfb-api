param([ValidateSet('seeker')][string]$Component='seeker')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'seeker-port.ps1')
$base=Join-Path $env:USERPROFILE 'SFB\seeker'
if (Test-SeekerBusy) {Write-Host 'Seeker already has a process holding its port. No second process started.';return}
$active=(Get-Content (Join-Path $base 'active.txt') -Raw).Trim()
$release=Join-Path (Join-Path $base 'releases') $active
$entry=Join-Path $release 'runtime.py'
$python=Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
if (!(Test-Path $python)) {$python='python.exe'}
$oldOpenAI=$env:OPENAI_KEY;$oldSupabase=$env:SUPABASE_KEY
try {
 foreach ($name in @('OPENAI_KEY','SUPABASE_KEY')) {
  $value=[Environment]::GetEnvironmentVariable("SFB_SEEKER_$name",'User')
  if (!$value) {$value=[Environment]::GetEnvironmentVariable($name,'User')}
  if (!$value) {throw "Missing credential: $name"}
  [Environment]::SetEnvironmentVariable($name,$value,'Process')
 }
 $env:SFB_COMPONENT_STATE=Join-Path $base 'state'
 $env:SFB_LOCKED_SEEKER_DIR=Join-Path $base 'package'
 Remove-Item (Join-Path $env:SFB_COMPONENT_STATE 'stop') -ErrorAction SilentlyContinue
 Write-Host "Starting Seeker release $active..."
 $child=Start-Process -FilePath $python -ArgumentList @('-u',('"'+$entry+'"')) -WorkingDirectory $release -RedirectStandardOutput (Join-Path $base 'worker.log') -RedirectStandardError (Join-Path $base 'worker.err') -WindowStyle Hidden -PassThru
 $deadline=(Get-Date).AddSeconds(30)
 do {
  if ($child.HasExited) {Get-Content (Join-Path $base 'worker.err') -Tail 20;throw 'Seeker exited during startup.'}
  $ready=Select-String -Path (Join-Path $base 'worker.log') -SimpleMatch "seeker ready; release $active;" -Quiet -ErrorAction SilentlyContinue
  if ($ready) {Write-Host "Seeker ready: $active (PID $($child.Id)).";return}
  Start-Sleep -Milliseconds 500
 } while ((Get-Date) -lt $deadline)
 Write-Host 'Startup requested, but readiness is not confirmed. Do not start a second copy.'
 Get-Content (Join-Path $base 'worker.err') -Tail 20
} finally {$env:OPENAI_KEY=$oldOpenAI;$env:SUPABASE_KEY=$oldSupabase}
