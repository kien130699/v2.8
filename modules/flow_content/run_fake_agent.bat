@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Hay chay run.bat 1 lan truoc de tao .venv.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python tools\fake_flow_agent.py
pause
