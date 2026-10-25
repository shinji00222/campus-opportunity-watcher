@echo off
chcp 65001 >nul
set "SCHOOL=%~1"
if "%SCHOOL%"=="" set /p "SCHOOL=请输入学校 id（广东工业大学是 gdut）："
if "%SCHOOL%"=="" exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -School "%SCHOOL%"
if errorlevel 1 pause
