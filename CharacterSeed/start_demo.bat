@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: 获取项目根目录
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

:: 检查虚拟环境
if not exist "%ROOT%\.venv\Scripts\activate.bat" (
    echo [错误] 未找到虚拟环境 .venv\Scripts\activate.bat
    pause
    exit /b 1
)
if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境的 Python 解释器
    pause
    exit /b 1
)

:: 激活虚拟环境
call "%ROOT%\.venv\Scripts\activate.bat"

cls
echo.
echo  ========================================
echo   CharacterSeed 一键启动
echo   后端：http://localhost:8000 ^(本窗口^)
echo   前端：http://localhost:8501 ^(新窗口^)
echo  ========================================
echo.

:: [1/4] 清理僵尸进程（上次运行时残留的 python 进程）
echo [1/4] 正在清理残留进程...
set KILLED=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000"') do (
    taskkill /f /pid %%p >nul 2>&1
    if !errorlevel! equ 0 (
        echo        已终止僵尸进程 PID: %%p
        set /a KILLED+=1
    )
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8501"') do (
    taskkill /f /pid %%p >nul 2>&1
    if !errorlevel! equ 0 (
        echo        已终止僵尸进程 PID: %%p
        set /a KILLED+=1
    )
)
if !KILLED! gtr 0 (
    echo        共清理 !KILLED! 个残留进程。
    timeout /t 1 /nobreak >nul
) else (
    echo        无残留进程，端口空闲。
)
echo.

:: [2/4] 清除缓存
echo [2/4] 正在清除 __pycache__ 缓存...
for /d /r "%ROOT%" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
echo        缓存清除完成。
echo.

:: [3/4] 启动前端（独立窗口）
echo [3/4] 正在启动前端应用（新窗口）...
start "CharacterSeed-Frontend" cmd /k "chcp 65001 >nul & cd /d "%ROOT%" & call ".venv\Scripts\activate.bat" & title CharacterSeed-Frontend & echo. & echo [前端应用启动中] & echo    地址: http://localhost:8501 & echo. & streamlit run frontend/app.py"
echo        前端窗口已打开。

:: [4/4] 启动后端（本窗口前台运行）
title CharacterSeed-Backend
echo [4/4] 正在启动后端服务（本窗口）...
echo.
echo ======================================== 后端日志 ========================================
echo.

uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

echo.
echo ========================================
echo  后端已停止。按任意键关闭本窗口...
echo ========================================
pause >nul
