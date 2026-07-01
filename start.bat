@echo off
chcp 65001 >nul
title Project Kei 启动

set "ROOT=%~dp0"

echo =============================================
echo   Project Kei
echo =============================================
echo.

:: ===== 1. SnowLuma =====
echo [1/2] 启动 SnowLuma ...
if not exist "%ROOT%snowluma\launcher.bat" (
    echo   [ERR] 未找到 SnowLuma，请解压 SnowLuma 到 snowluma 目录
    pause
    exit /b 1
)

:: 检查 SnowLuma 是否已在运行（通过 WebUI 端口 5099/5100）
netstat -ano 2>nul | findstr /C:"LISTENING" | findstr /C:":5099 " /C:":5100 " >nul
if %errorlevel% equ 0 (
    echo   [SKIP] SnowLuma 已在运行
    goto snowluma_done
)

:: 生成隐藏启动 VBS
set "VBS=%TEMP%\sl_hidden.vbs"
(echo CreateObject("WScript.Shell"^).Run "cmd /c cd /d ""%ROOT%snowluma"" && launcher.bat", 0, False) > "%VBS%"
cscript //nologo "%VBS%"
del "%VBS%"
echo   [OK] SnowLuma 已启动（后台）

:: 自动打开 WebUI（5099 优先，其次 5100）
echo   打开管理面板 ...
netstat -ano 2>nul | findstr /C:":5099 " | findstr /C:"LISTENING" >nul && (
    start http://127.0.0.1:5099
) || (
    start http://127.0.0.1:5100
)

:snowluma_done

:: ===== 2. NoneBot =====
echo [2/2] 启动 NoneBot ...
timeout /t 1 /nobreak >nul

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
