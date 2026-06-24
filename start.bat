@echo off
chcp 65001 >nul
title Project Kei 启动

set "ROOT=%~dp0"

echo =============================================
echo   Project Kei
echo =============================================
echo.

:: ===== 1. NapCat =====
echo [1/2] 启动 NapCatQQ ...
for /d %%i in ("%ROOT%napcat\NapCat.*.Shell") do set "NC=%%i"
if not defined NC (
    echo   [ERR] 未找到 NapCat 目录
    pause
    exit /b 1
)

:: 生成隐藏启动 VBS
set "VBS=%TEMP%\nc_hidden.vbs"
(echo CreateObject("WScript.Shell"^).Run "cmd /c cd /d ""%NC%"" && napcat.bat", 0, False) > "%VBS%"
cscript //nologo "%VBS%"
del "%VBS%"
echo   [OK] NapCatQQ 已启动（后台）

:: 自动打开 WebUI
echo   打开管理面板 ...
start http://127.0.0.1:6099/webui/

:: ===== 2. NoneBot =====
echo [2/2] 启动 NoneBot ...
timeout /t 8 /nobreak >nul

set "VBS=%TEMP%\nb_hidden.vbs"
(echo CreateObject("WScript.Shell"^).Run "cmd /c cd /d ""%ROOT%bot"" && ""%ROOT%.venv\Scripts\python.exe"" bot.py", 0, False) > "%VBS%"
cscript //nologo "%VBS%"
del "%VBS%"

echo.
echo   全部启动完成（后台运行）
echo   托盘右键可管理机器人
echo   5秒后自动关闭此窗口
timeout /t 5 /nobreak >nul
exit
