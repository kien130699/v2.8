@echo off
setlocal
cd /d "%~dp0"

rem Always give V2.8 its own persistent console. This window stays open even if
rem Python/Uvicorn exits, so startup/crash output cannot disappear.
if /I not "%~1"=="__inner" (
  start "V2.8 SERVER 3000" cmd.exe /k ""%~f0" __inner"
  exit /b 0
)

title V2.8.6.0 Facebook Job Factory - PORT 3000
if not exist .env copy /Y .env.example .env >nul
if not exist .venv\Scripts\python.exe (
  echo [V2.8] Tao virtualenv...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist .venv\.deps_v2860 (
  echo [V2.8.6.0] Kiem tra/cai dependency...
  .venv\Scripts\python.exe -m pip install -U pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :err
  type nul > .venv\.deps_v2860
)
where ffmpeg >nul 2>&1 || echo [WARNING] Khong tim thay ffmpeg trong PATH. Render co the loi.
set V28_PORT=3000
if "%V28_EDGE_DEBUG_PORT%"=="" set V28_EDGE_DEBUG_PORT=9224
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "$u='http://127.0.0.1:%V28_PORT%/api/health'; for($i=0;$i -lt 90;$i++){try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 $u;if($r.StatusCode -eq 200){Start-Process 'http://127.0.0.1:%V28_PORT%';break}}catch{};Start-Sleep -Milliseconds 500}" >nul 2>&1

echo.
echo ================================================
echo V2.8.6.0: http://127.0.0.1:%V28_PORT%
echo Flow WS: ws://127.0.0.1:%V28_PORT%/ws/flow
echo Edge debug owner: %V28_EDGE_DEBUG_PORT%
echo Console log: data\server_console.log
echo Crash log  : data\server_crash.log
echo ================================================
echo.
echo [ENV] Kiem tra key an toan, KHONG in gia tri secret:
call ENV_CHECK.bat --no-pause
echo.
.venv\Scripts\python.exe supervisor.py
set RC=%ERRORLEVEL%
echo.
echo [V2.8.6.0] Supervisor da dung · code=%RC%
echo Console log: data\server_console.log
echo Crash log  : data\server_crash.log
echo.
echo Cua so nay duoc mo bang CMD /K nen se giu nguyen.
if not "%RC%"=="0" echo [ERROR] Kiem tra log o tren.
goto :eof

:err
echo [ERROR] Cai dependency that bai.
echo Cua so nay van duoc giu mo.
goto :eof
