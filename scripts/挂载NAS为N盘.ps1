# PowerShell version: start proxy if needed and map N:
$scripts = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scripts
$py = if ($env:MIMO_PYTHON) { $env:MIMO_PYTHON } else { "python" }
if (-not (Get-NetTCPConnection -LocalPort 8899 -State Listen -ErrorAction SilentlyContinue)) {
  Start-Process -FilePath $py -ArgumentList "-m","minas_webdav.proxy" -WorkingDirectory $root -WindowStyle Hidden
  Start-Sleep -Seconds 2
}
net use N: /delete /y 2>$null | Out-Null
net use N: "http://127.0.0.1:8899/pool0/data" /persistent:yes
if (Test-Path "N:\") { Write-Host "N: OK" } else { Write-Host "N: FAILED" }
