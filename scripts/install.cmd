@echo off
rem Double-click installer for Windows.
rem Wraps install.ps1 with the execution-policy flag so users do not need PowerShell knowledge.
rem Keep this file ASCII-only.

setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
    echo.
    echo Installation failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo Installation finished.
pause
endlocal
