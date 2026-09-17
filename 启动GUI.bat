@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

uv run --locked workbuddy-sync gui
if errorlevel 1 (
    echo.
    echo [ERROR] The GUI failed to start. Review the output above.
    pause
    exit /b 1
)

endlocal
