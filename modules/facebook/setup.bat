@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo [ERROR] Khong tim thay Python & pause & exit /b 1)
if not exist .venv (
  echo [1/3] Tao virtual environment...
  python -m venv .venv || exit /b 1
)
echo [2/3] Cai dependencies...
.venv\Scripts\python.exe -m pip install -r requirements.txt || (echo [ERROR] pip install that bai & pause & exit /b 1)
if not exist .env copy /Y .env.example .env >nul
echo [3/3] OK. Timezone Windows da duoc cai qua tzdata.
echo Sau do chay start_web.bat hoac run_server.bat
pause
