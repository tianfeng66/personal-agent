@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" copy /y ".env.example" ".env" >nul
if exist "runtime\python.exe" (
  "runtime\python.exe" -X utf8 -m agent.service_windows desktop 8765
) else (
  py -3 -X utf8 -m agent.service_windows desktop 8765
)
if errorlevel 1 pause
