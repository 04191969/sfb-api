param([ValidateSet('live_matches','seeker')][string]$Component)
$ErrorActionPreference='Stop'
$base=Join-Path $env:USERPROFILE "SFB\$Component"
New-Item -ItemType File -Force (Join-Path $base 'state\stop') | Out-Null
Write-Host "$Component will finish its current job and stop. The other worker is unaffected."
