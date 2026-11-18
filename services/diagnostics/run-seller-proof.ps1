param([ValidateSet('installed','ddgs')][string]$SearchProvider='installed')
$ErrorActionPreference='Stop'
$python='C:\Users\drjul\AppData\Local\Python\pythoncore-3.14-64\python.exe'
if (!(Test-Path $python)) {$python='python.exe'}
Write-Host 'Public seller discovery only. No paid model calls, database writes, worker restart or production changes.'
& $python -u (Join-Path $PSScriptRoot 'seller_discovery_probe.py') --provider $SearchProvider
if ($LASTEXITCODE -ne 0) {throw 'Probe setup failed. Keep the report printed above.'}
