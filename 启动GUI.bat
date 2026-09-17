@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'uv' -ArgumentList @('run','--locked','workbuddy-sync','gui') -WorkingDirectory '%~dp0' -WindowStyle Hidden"

endlocal
