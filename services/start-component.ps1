param([ValidateSet('live_matches','seeker')][string]$Component)
$ErrorActionPreference='Stop'
$base=Join-Path $env:USERPROFILE "SFB\$Component"
$active=Get-Content (Join-Path $base 'active.txt') -Raw
$release=Join-Path (Join-Path $base 'releases') $active.Trim()
$entry=Join-Path $release 'runtime.py'
$running=Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($entry) }
if ($running) { exit 0 }
$python='C:\Users\drjul\AppData\Local\Python\pythoncore-3.14-64\python.exe'
if (!(Test-Path $python)) { $python='python.exe' }
$env:SFB_COMPONENT_STATE=Join-Path $base 'state'
New-Item -ItemType Directory -Force $env:SFB_COMPONENT_STATE | Out-Null
Remove-Item (Join-Path $env:SFB_COMPONENT_STATE 'stop') -ErrorAction SilentlyContinue
$prefix=if ($Component -eq 'live_matches') {'SFB_LIVE'} else {'SFB_SEEKER'}
foreach ($name in @('OPENAI_KEY','SUPABASE_KEY')) {
 $value=[Environment]::GetEnvironmentVariable("${prefix}_$name",'User')
 if (!$value) {$value=[Environment]::GetEnvironmentVariable($name,'User')}
 if (!$value) {throw "Missing credential: $name"}
 [Environment]::SetEnvironmentVariable($name,$value,'Process')
}
$env:SFB_LOCKED_SEEKER_DIR=Join-Path $base 'package'
$log=Join-Path $base 'worker.log';$err=Join-Path $base 'worker.err'
try {
 Start-Process -FilePath $python -ArgumentList @('-u',('"'+$entry+'"')) -WorkingDirectory $release -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden
} finally {Remove-Item Env:OPENAI_KEY,Env:SUPABASE_KEY -ErrorAction SilentlyContinue}
