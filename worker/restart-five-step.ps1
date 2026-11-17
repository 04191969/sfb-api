$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$package = Join-Path $env:USERPROFILE 'Documents\test it\Seeker_v0_9_1_QQ2_handoff_fix\Seeker_locked_v0_9_1_plus_QQ2'
if ($env:SFB_LOCKED_SEEKER_DIR) { $package = $env:SFB_LOCKED_SEEKER_DIR }
$manifest = Get-Content (Join-Path $PSScriptRoot 'five_step_package_hashes.json') -Raw | ConvertFrom-Json
foreach ($entry in $manifest.PSObject.Properties) {
    $file = Join-Path $package $entry.Name
    if (!(Test-Path -LiteralPath $file)) { throw "Package file missing: $($entry.Name). Worker was not stopped." }
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $entry.Value) { throw "Package differs: $($entry.Name). Worker was not stopped." }
}
$python = "C:\Users\drjul\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if (!(Test-Path $python)) { $python = 'python.exe' }
$oldKey = $env:SUPABASE_KEY
try {
    $env:SUPABASE_KEY = [Environment]::GetEnvironmentVariable('SUPABASE_KEY', 'User')
    $check = @'
import json, os, sys
try:
    from supabase import create_client
    key = os.environ.get('SUPABASE_KEY', '').strip()
    if not key:
        print('Missing user credential. Worker was not stopped.')
        sys.exit(2)
    url = os.environ.get('SUPABASE_URL', 'https://fyxglozykvhrfejrcrqz.supabase.co').strip()
    sb = create_client(url, key)
    rows = sb.table('seeker_runs').select('id,status').in_('status', ['pending', 'running']).limit(1).execute().data
    if rows:
        print('Pending or running jobs exist. Worker was not stopped.')
        sys.exit(3)
    print('Queue verified using the worker Supabase client.')
except Exception as exc:
    code = getattr(exc, 'code', None)
    safe_code = str(code) if str(code).isalnum() and len(str(code)) <= 12 else 'unknown'
    print('Queue verification failed: ' + type(exc).__name__ + '; code=' + safe_code + '. Worker was not stopped.')
    sys.exit(4)
'@
    $check | & $python -
    if ($LASTEXITCODE -ne 0) { throw 'Queue check did not pass. Worker was not stopped.' }
} finally {
    $env:SUPABASE_KEY = $oldKey
    Remove-Variable oldKey -ErrorAction SilentlyContinue
}
$running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like '*SFB_Seeker_v2_env.bat*' }
$running | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction Stop }
$env:PYTHONUNBUFFERED = '1'
& (Join-Path $PSScriptRoot 'start-seeker.ps1')
Write-Host 'Restart requested. Verify the fresh heartbeat and one blank Run Seeker before enabling scheduling.'
