@echo off
setlocal
chcp 65001 >nul
title Primee Voice - Haaniye TTS setup
set "SCRIPT=%~dp0tools\voice\Start-PrimeeVoice.ps1"
if not exist "%SCRIPT%" (
  echo Start-PrimeeVoice.ps1 was not found next to this launcher.
  echo Extract the complete ZIP first, then double-click this file again.
  pause
  exit /b 1
)
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (echo Finished successfully.) else (echo Finished with errors. Exit code %RC%.)
pause
exit /b %RC%
