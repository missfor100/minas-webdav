@echo off
rem Re-mount Xiaomi NAS as N: (user session, no admin needed)
setlocal
set PY=%MIMO_PYTHON%
if "%PY%"=="" set PY=python
set ROOT=%~dp0..

rem start proxy if port 8899 not listening
powershell -NoProfile -Command "if (-not (Get-NetTCPConnection -LocalPort 8899 -State Listen -ErrorAction SilentlyContinue)) { Start-Process -FilePath '%PY%' -ArgumentList '-m','minas_webdav.proxy' -WorkingDirectory '%ROOT%' -WindowStyle Hidden; Start-Sleep -Seconds 2 }"

rem always remap so it appears in THIS session / Explorer
net use N: /delete /y >nul 2>&1
net use N: http://127.0.0.1:8899/pool0/data /persistent:yes

rem force Explorer to refresh drives
powershell -NoProfile -Command "Stop-Process -Name explorer -Force; Start-Process explorer"
endlocal
