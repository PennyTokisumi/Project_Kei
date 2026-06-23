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

:: ===== 1. 启动 NapCatQQ =====
echo [1/2] 启动 NapCatQQ...
for /d %%i in ("%ROOT%\napcat\NapCat.*.Shell") do set "NAPCAT_DIR=%%i"
if not exist "%NAPCAT_DIR%\napcat.bat" (
    echo   ! 未找到 NapCatQQ，请先安装
) else (
    start "NapCat" cmd /c "cd /d "%NAPCAT_DIR%" && napcat.bat 2>&1"
)
timeout /t 5 /nobreak >nul

:: ===== 2. 启动 NoneBot2 =====
echo [2/2] 启动 NoneBot2 (WebSocket 端口 8080)...
start "NoneBot2" cmd /c "cd /d "%ROOT%\bot" && "%ROOT%\.venv\Scripts\python.exe" bot.py 2>&1"

echo.
echo 所有组件已启动！
echo   NapCatQQ: WebUI http://127.0.0.1:6099/webui
echo   NoneBot2: WebSocket 8080
echo.
echo 关闭所有窗口即可停止，或运行 stop.bat
echo ========================================
pause
