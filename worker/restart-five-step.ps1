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
$key = [Environment]::GetEnvironmentVariable('SUPABASE_KEY', 'User')
if (!$key) { throw 'Supabase credential missing from user environment. Worker was not stopped.' }
try {
    $rows = Invoke-RestMethod -Uri 'https://fyxglozykvhrfejrcrqz.supabase.co/rest/v1/seeker_runs?select=id,status&status=in.(pending,running)' -Headers @{apikey=$key;Authorization="Bearer $key"}
} catch { throw 'Could not verify queue state. Worker was not stopped.' }
if (@($rows).Count -gt 0) { throw 'Pending or running jobs exist. Worker was not stopped. Review the run status first.' }
$running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like '*SFB_Seeker_v2_env.bat*' }
$running | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction Stop }
$env:PYTHONUNBUFFERED = '1'
& (Join-Path $PSScriptRoot 'start-seeker.ps1')
Write-Host 'Restart requested. Verify the fresh heartbeat and one blank Run Seeker before enabling scheduling.'
