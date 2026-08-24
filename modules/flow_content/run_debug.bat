@echo off
setlocal
cd /d "%~dp0"
title Flow Content Factory DEBUG V2.13.4
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist .venv\Scripts\python.exe (
  echo Chua co .venv. Hay chay run.bat truoc.
  pause
  exit /b 1
)

echo === DEBUG START ===
echo Folder: %CD%
echo.
.venv\Scripts\python.exe --version
.venv\Scripts\python.exe -c "import sys; print(sys.executable); from zoneinfo import ZoneInfo; print('timezone:', ZoneInfo('Asia/Ho_Chi_Minh')); print('stdout:', sys.stdout.encoding)"
echo.

.venv\Scripts\python.exe run_server.py
set "APPERR=%ERRORLEVEL%"
echo.
echo Exit code: %APPERR%
echo Log: %CD%\server_crash.log
if not "%APPERR%"=="0" pause
exit /b %APPERR%
