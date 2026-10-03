@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\recover_campaign.py" %*
) else (
    python "scripts\recover_campaign.py" %*
)
set "RECOVERY_EXIT=%ERRORLEVEL%"

echo.
pause
exit /b %RECOVERY_EXIT%
