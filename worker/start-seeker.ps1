# Start the Seeker worker when it is not already running.
# Automatic category search stays off. Keys stay in the user environment.
$bat = Join-Path $PSScriptRoot "SFB_Seeker_v2_env.bat"
$python = "C:\Users\drjul\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*SFB_Seeker_v2_env.bat*" }
if ($running) { exit 0 }
if (-not (Test-Path $python)) { $python = "python.exe" }
function Get-UserEnv([string]$name) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment")
    try { return [string]$key.GetValue($name) } finally { $key.Close() }
}
$env:SEEKER_AUTO_SEARCH = "0"
$env:OPENAI_KEY = Get-UserEnv "OPENAI_KEY"
$env:SUPABASE_KEY = Get-UserEnv "SUPABASE_KEY"
# Only the locked API discovery child uses these; never log their values.
foreach ($name in @('EBAY_CLIENT_ID', 'EBAY_CLIENT_SECRET')) {
    $value = Get-UserEnv $name
    if ($value) { [Environment]::SetEnvironmentVariable($name, $value, 'Process') }
}
Remove-Variable value -ErrorAction SilentlyContinue
if (-not $env:OPENAI_KEY -or -not $env:SUPABASE_KEY) { exit 1 }
$log = Join-Path $env:TEMP "sfb-seeker-start.log"
$err = Join-Path $env:TEMP "sfb-seeker-start.err"
Start-Process -FilePath $python -ArgumentList @("-x", $bat) -WorkingDirectory (Split-Path $PSScriptRoot -Parent) -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden
Remove-Item Env:OPENAI_KEY -ErrorAction SilentlyContinue
Remove-Item Env:SUPABASE_KEY -ErrorAction SilentlyContinue
