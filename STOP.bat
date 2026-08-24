@echo off
setlocal
cd /d "%~dp0"
if not exist data mkdir data
> data\v28_stop.flag echo stop
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ids=@(); foreach($f in @('data\v28_server.pid','data\v28_supervisor.pid')){if(Test-Path $f){$v=(Get-Content $f -ErrorAction SilentlyContinue | Select-Object -First 1); if($v -match '^\d+$'){$ids += [int]$v}}}; $port=if($env:V28_PORT){[int]$env:V28_PORT}else{3000}; $p=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; if($p){$ids += $p}; $ids=$ids|Select-Object -Unique; foreach($id in $ids){Stop-Process -Id $id -Force -ErrorAction SilentlyContinue}; Remove-Item 'data\v28_server.pid','data\v28_supervisor.pid' -Force -ErrorAction SilentlyContinue; if($ids){Write-Host ('[V2.8.5.7] Da dung PID: '+($ids -join ','))}else{Write-Host '[V2.8.5.7] Server khong chay'}"
exit /b 0
