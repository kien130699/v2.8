@echo off
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  call setup.bat
  if errorlevel 1 exit /b 1
)

rem Windows CPython usually has no IANA timezone database. Self-heal old venvs.
.venv\Scripts\python.exe -c "import tzdata" >nul 2>nul
if errorlevel 1 (
  echo [FIX] Thieu tzdata - dang cai timezone database cho Windows...
  .venv\Scripts\python.exe -m pip install "tzdata>=2026.1,<2027"
  if errorlevel 1 (
    echo [WARN] Khong cai duoc tzdata. Server van co fallback UTC+7 cho Asia/Ho_Chi_Minh.
  )
)

start "Facebook Factory Server" cmd /k "cd /d %~dp0 && .venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8797"
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8797
