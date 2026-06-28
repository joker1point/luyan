<#
.SYNOPSIS
    CharacterSeed 一键启动 — 文件日志 + 实时 tail 方案
.DESCRIPTION
    后端 uvicorn 输出重定向到 logs/backend.log，
    同时另开一个 PowerShell 窗口实时 tail 该日志文件。
    前端在独立窗口中运行。
.NOTES
    运行: powershell -ExecutionPolicy Bypass -File start_demo.ps1
#>

$ErrorActionPreference = "Continue"
$ROOT = $PSScriptRoot

# 确保 logs 目录存在
$logsDir = Join-Path $ROOT "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }

$venvActivate = Join-Path $ROOT ".venv\Scripts\Activate.ps1"
$pythonExe    = Join-Path $ROOT ".venv\Scripts\python.exe"
$logFile      = Join-Path $logsDir "backend.log"

# 清空旧日志
if (Test-Path $logFile) { Clear-Content $logFile -Force }

# 校验
if (-not (Test-Path $venvActivate)) {
    Write-Host "[错误] 未找到虚拟环境" -ForegroundColor Red; Read-Host "按 Enter 退出"; exit 1
}

$Host.UI.RawUI.WindowTitle = "CharacterSeed 启动器"
Write-Host ""
Write-Host " ========================================" -ForegroundColor Cyan
Write-Host "   CharacterSeed 一键启动" -ForegroundColor Cyan
Write-Host "   后端: http://localhost:8000" -ForegroundColor White
Write-Host "   前端: http://localhost:8501" -ForegroundColor White
Write-Host "   日志: $logFile" -ForegroundColor DarkGray
Write-Host " ========================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# 1. 清除缓存
# ============================================================
Write-Host "[1/4] 清除 __pycache__ 缓存..." -ForegroundColor Yellow
Get-ChildItem -Path $ROOT -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "      缓存清除完成" -ForegroundColor Green

# ============================================================
# 2. 启动前端（独立窗口）
# ============================================================
Write-Host "[2/4] 启动前端应用..." -ForegroundColor Yellow

$frontendCmd = @"
`$ErrorActionPreference = 'Continue'
`$Host.UI.RawUI.WindowTitle = 'CharacterSeed-Frontend'
Set-Location '$ROOT'
. '$venvActivate'
`$env:PYTHONUNBUFFERED = 1
Write-Host ''
Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  CharacterSeed 前端 (Streamlit)' -ForegroundColor Cyan
Write-Host '  http://localhost:8501' -ForegroundColor White
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ''
streamlit run frontend/app.py 2>&1
"@

Start-Process powershell -ArgumentList "-NoExit","-NoProfile","-Command",$frontendCmd
Write-Host "      前端窗口已打开" -ForegroundColor Green

# ============================================================
# 3. 启动后端（文件日志）
# ============================================================
Write-Host "[3/4] 启动后端服务（日志 → logs/backend.log）..." -ForegroundColor Yellow

$backendCmd = @"
`$ErrorActionPreference = 'Continue'
`$Host.UI.RawUI.WindowTitle = 'CharacterSeed-Backend'
Set-Location '$ROOT'
. '$venvActivate'
`$env:PYTHONUNBUFFERED = 1

Write-Host '========================================'
Write-Host '  CharacterSeed 后端 (FastAPI)'
Write-Host '  http://localhost:8000'
Write-Host '  日志输出: logs/backend.log'
Write-Host '  （本窗口仅显示 stderr，完整日志请查看日志窗口）'
Write-Host '========================================'
Write-Host ''

# stdout 写入日志文件，stderr 保留在控制台
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000 --log-level info --access-log --use-colors *>> '$logFile' 2>&1
"@

$backendProc = Start-Process powershell -ArgumentList "-NoExit","-NoProfile","-Command",$backendCmd -PassThru
Write-Host "      后端窗口已打开 (PID: $($backendProc.Id))" -ForegroundColor Green

# ============================================================
# 4. 启动日志 tail 窗口
# ============================================================
Write-Host "[4/4] 启动日志实时查看窗口..." -ForegroundColor Yellow

# 等待日志文件出现
$waited = 0
while (-not (Test-Path $logFile) -and $waited -lt 10) {
    Start-Sleep -Milliseconds 500; $waited++
}

$tailCmd = @"
`$ErrorActionPreference = 'Continue'
`$Host.UI.RawUI.WindowTitle = 'CharacterSeed-日志'
Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  CharacterSeed 后端日志 (实时 tail)' -ForegroundColor Cyan
Write-Host "  文件: $logFile" -ForegroundColor DarkGray
Write-Host '  Ctrl+C 停止 tail，关闭窗口不影响后端' -ForegroundColor DarkGray
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ''

Get-Content -Path '$logFile' -Wait -Encoding UTF8
"@

Start-Process powershell -ArgumentList "-NoExit","-NoProfile","-Command",$tailCmd
Write-Host "      日志窗口已打开" -ForegroundColor Green

# ============================================================
# 完成
# ============================================================
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  启动完成！共 3 个窗口：" -ForegroundColor Green
Write-Host "    1. 后端服务    2. 日志实时查看    3. 前端应用" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "按 Enter 关闭本启动器"
