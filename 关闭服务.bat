@echo off
chcp 65001 >nul
title Stop Monitor Services (port 8000 / 5173)
echo ============================================================
echo   Stop Excavation Monitoring System services
echo   Backend port 8000 / Frontend port 5173
echo ============================================================

setlocal enabledelayedexpansion
set KILLED=0

echo [1/2] Stopping backend (8000)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "127.0.0.1:8000" ^| findstr "LISTENING"') do (
    echo   - backend PID %%p
    taskkill /PID %%p /F /T >nul 2>&1
    set KILLED=1
)

echo [2/2] Stopping frontend (5173)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "127.0.0.1:5173" ^| findstr "LISTENING"') do (
    echo   - frontend PID %%p
    taskkill /PID %%p /F /T >nul 2>&1
    set KILLED=1
)

if "!KILLED!"=="1" (
    echo.
    echo Done. Services stopped.
) else (
    echo.
    echo No running service found on 8000 / 5173 (already stopped?).
)
timeout /t 2 /nobreak >nul
