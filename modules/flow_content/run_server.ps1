$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$app = Join-Path $PSScriptRoot 'app.py'
$log = Join-Path $PSScriptRoot 'server_crash.log'

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Khong tim thay Python venv: $python" -ForegroundColor Red
    exit 2
}
if (-not (Test-Path -LiteralPath $app)) {
    Write-Host "Khong tim thay app.py: $app" -ForegroundColor Red
    exit 3
}

if (Test-Path -LiteralPath $log) {
    Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
}

Write-Host "[RUN] $python app.py"
Write-Host "[LOG] $log"
Write-Host ""

& $python $app 2>&1 | Tee-Object -FilePath $log
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 1 }
exit $code
