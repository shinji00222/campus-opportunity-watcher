@echo off
chcp 65001 >nul
set "SCHOOL=%~1"
if "%SCHOOL%"=="" set /p "SCHOOL=请输入要卸载的学校 id（例如 gdut）："
if "%SCHOOL%"=="" exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" -School "%SCHOOL%"
pause
