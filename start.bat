@echo off
chcp 65001 >nul
title Project Kei 启动

set "ROOT=%~dp0"

echo =============================================
echo   Project Kei
echo =============================================
echo.

:: ===== 启动 =====
echo 启动 Project Kei ...
set "VBS=%TEMP%\nb_hidden.vbs"
(echo CreateObject("WScript.Shell"^).Run "cmd /c cd /d ""%ROOT%bot"" && ""%ROOT%.venv\Scripts\python.exe"" bot.py", 0, False) > "%VBS%"
cscript //nologo "%VBS%"
del "%VBS%"

echo.
echo   等待 Kei 启动完成...
set "SIGNAL=%ROOT%data\.startup_ok"
if exist "%SIGNAL%" del "%SIGNAL%"
:wait_loop
timeout /t 1 /nobreak >nul
if not exist "%SIGNAL%" goto wait_loop

:: 检查是否重复启动
findstr /c:"DUPLICATE" "%SIGNAL%" >nul 2>&1
if %errorlevel% equ 0 (
    del "%SIGNAL%"
    echo   Kei 已在运行中，无需重复启动。
    timeout /t 2 /nobreak >nul
    exit /b 1
)

del "%SIGNAL%"
echo   KEI 启动成功
echo   托盘右键可管理机器人
echo   2秒后自动关闭此窗口
timeout /t 2 /nobreak >nul
exit
