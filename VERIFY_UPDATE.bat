@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo V2.8.5.9 UPDATE VERIFY
echo ================================================
findstr /C:"2.8.5.9-windows-utf8-engine" BUILD_INFO.json >nul && echo [OK] Server build 2.8.5.9 || echo [FAIL] BUILD_INFO khong phai 2.8.5.9
findstr /C:"\"version\": \"14.7.0\"" extensions\FLOW_WORKER\manifest.json >nul && echo [OK] FLOW_WORKER file = 14.7.0 || echo [FAIL] FLOW_WORKER file khong phai 14.7.0
echo [PORT] V2.8=3000 / Edge=9224 / KHONG DUNG 8786,8787 / KHONG DUNG Edge 9222,9223
echo [ROOT] %CD%
echo ================================================
pause
