@echo off
setlocal
chcp 65001 >nul
title Medical Literature Tracker - One Click Setup
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\install.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo Installation completed successfully.
) else (
  echo Installation stopped with error code %EXIT_CODE%.
  echo See the log shown above for details.
)
pause
exit /b %EXIT_CODE%
