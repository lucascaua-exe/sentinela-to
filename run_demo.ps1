# Sentinela TO - inicia backend + simulador IoT e abre o dashboard
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "[Sentinela TO] Criando ambiente virtual..." -ForegroundColor Yellow
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

$py = ".\.venv\Scripts\python.exe"
Write-Host "[Sentinela TO] Iniciando backend em http://localhost:8000 ..." -ForegroundColor Yellow
$backend = Start-Process $py -ArgumentList "-m", "uvicorn", "backend.main:app", "--port", "8000" -PassThru

Start-Sleep -Seconds 4
Write-Host "[Sentinela TO] Iniciando simulador IoT (4 unidades)..." -ForegroundColor Yellow
$simulator = Start-Process $py -ArgumentList "simulator\iot_emulator.py" -PassThru

Start-Process "http://localhost:8000"
Write-Host "[Sentinela TO] Dashboard aberto. Pressione ENTER para encerrar a demonstração." -ForegroundColor Green
Read-Host | Out-Null

Stop-Process -Id $simulator.Id, $backend.Id -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "uvicorn backend.main:app|iot_emulator.py" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "[Sentinela TO] Demonstração encerrada."
