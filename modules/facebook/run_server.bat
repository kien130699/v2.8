@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo [ERROR] Chua setup. Hay chay setup.bat truoc.
  pause
  exit /b 1
)
if not exist .env copy /Y .env.example .env >nul

rem Repair timezone dependency for existing Windows virtual environments.
.venv\Scripts\python.exe -c "import tzdata" >nul 2>nul
if errorlevel 1 (
  echo [FIX] Thieu tzdata - dang cai timezone database cho Windows...
  .venv\Scripts\python.exe -m pip install "tzdata>=2026.1,<2027"
  if errorlevel 1 echo [WARN] Cai tzdata that bai; config.py se fallback UTC+7 cho Viet Nam.
)

set PYTHONUTF8=1
.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8797
if errorlevel 1 (
  echo.
  echo [ERROR] Server dung bat thuong.
  pause
)
