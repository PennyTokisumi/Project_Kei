@echo off
chcp 65001 >nul
title QQ_Monitor_Bot 启动面板

echo ========================================
echo   QQ_Monitor_Bot - 启动所有组件
echo ========================================
echo.

:: ===== 获取脚本所在目录 =====
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

:: ===== 1. 启动 RSSHub =====
echo [1/3] 启动 RSSHub (端口 1200)...
start "RSSHub" cmd /c "cd /d "%ROOT%\rsshub" && npm start 2>&1"
if %ERRORLEVEL% NEQ 0 (
    echo   ! RSSHub 启动失败，请检查是否已安装依赖 (cd rsshub ^&^& npm install)
)
timeout /t 3 /nobreak >nul

:: ===== 2. 启动 Lagrange.OneBot =====
echo [2/3] 启动 Lagrange.OneBot (端口 9090)...
start "Lagrange" cmd /c "cd /d "%ROOT%\lagrange" && Lagrange.OneBot.exe 2>&1"
timeout /t 3 /nobreak >nul

:: ===== 3. 启动 NoneBot2 =====
echo [3/3] 启动 NoneBot2 (WebSocket 端口 8080)...
start "NoneBot2" cmd /c "cd /d "%ROOT%\bot" && "%ROOT%\.venv\Scripts\python.exe" bot.py 2>&1"

echo.
echo 所有组件已启动！
echo   RSSHub:      http://127.0.0.1:1200
echo   Lagrange:    WebSocket 9090 -^> 8080
echo   NoneBot2:    WebSocket 8080
echo.
echo 关闭所有窗口即可停止，或运行 stop.bat
echo ========================================
pause
