@echo off
chcp 65001 >nul
if "%~1"=="" (
  echo 用法：uninstall.cmd 学校id，例如 uninstall.cmd gdut
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" -School %1
pause
