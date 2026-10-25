@echo off
chcp 65001 >nul
if "%~1"=="" (
  echo 用法：install.cmd 学校id，例如 install.cmd gdut
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -School %1
if errorlevel 1 pause
