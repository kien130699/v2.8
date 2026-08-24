@echo off
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=py
"%PY%" -c "from core.env_loader import load_project_env,env_status,env_file_info; load_project_env(); print('ENV:',env_file_info()); [print(k, 'configured='+str(v['configured']), 'source='+v['source'], ('path='+v.get('source_path','') if v.get('source_path') else '')) for k,v in env_status('9ROUTER_API_KEY','PEXELS_API_KEY','PIXABAY_API_KEY','SERPER_API_KEY').items()]"
if /I "%~1"=="--no-pause" exit /b %ERRORLEVEL%
echo.
echo Neu key=True thi V2.8 da doc duoc key. Gia tri secret KHONG duoc in.
pause
