@echo off
chcp 65001 >nul
title 基坑监测系统 - 一键启动（关窗即停）
cd /d "%~dp0"

echo ============================================================
echo   基坑拉森钢板桩沉降与水平位移观测系统
echo   正在启动后端 + 前端，请稍候...
echo   关闭本窗口 = 停止全部服务
echo ============================================================

set "PYEXE="

rem ---- 依次尝试候选 Python（不要写死单一路径，目录变动会直接启动失败）----
rem 1) WorkBuddy 托管环境的默认 venv（本项目依赖装在这里）
if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
    set "PYEXE=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
    goto :found
)

rem 2) 托管目录下任意 python（版本号会随升级变化，故用通配探测）
for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
)

rem 3) 项目自带的 .venv
if not defined PYEXE if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"

rem 4) 系统 PATH
if not defined PYEXE for /f "delims=" %%p in ('where python 2^>nul') do (
    if not defined PYEXE set "PYEXE=%%p"
)

:found
if not defined PYEXE (
    echo.
    echo [错误] 未找到 Python 解释器，无法启动。
    echo.
    echo   请任选一种方式解决：
    echo     1. 安装 Python 3.11+ 并勾选 "Add python.exe to PATH"
    echo     2. 在本目录执行以下命令创建专用环境：
    echo          python -m venv .venv
    echo          .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

rem ---- 依赖预检：缺依赖时给出可照抄的修复命令，而不是抛一堆 traceback ----
"%PYEXE%" -c "import fastapi, sqlalchemy, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 当前 Python 缺少项目依赖：
    echo        %PYEXE%
    echo.
    echo   请执行：
    echo        "%PYEXE%" -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

"%PYEXE%" "%~dp0launcher.py"

echo.
echo 服务已停止。
pause
