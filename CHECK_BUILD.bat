@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (set PY=.venv\Scripts\python.exe) else (set PY=python)
%PY% -m compileall -q core job_types master run_server.py supervisor.py modules\facebook\server.py modules\flow_content\app.py modules\parenting\app.py
if errorlevel 1 (echo CHECK FAIL & exit /b 1)
where node >nul 2>&1 && (
  node --check master\static\app.js || exit /b 1
  node --check extensions\FLOW_WORKER\background.js || exit /b 1
  node --check extensions\FLOW_WORKER\popup.js || exit /b 1
  node --check extensions\FLOW_WORKER\page.js || exit /b 1
)
%PY% tools\self_test.py || (echo SELF TEST FAIL & exit /b 1)
echo CHECK + SELF TEST OK
