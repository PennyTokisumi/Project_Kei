@echo off
chcp 65001 >nul
title QQ_Monitor_Bot 停止

echo 正在停止 QQ_Monitor_Bot 所有组件...
echo.

taskkill /FI "WINDOWTITLE eq NoneBot2" /F 2>nul && echo [OK] 已停止 NoneBot2 || echo [!] NoneBot2 未运行
taskkill /FI "WINDOWTITLE eq NapCat" /F 2>nul && echo [OK] 已停止 NapCatQQ || echo [!] NapCatQQ 未运行
taskkill /FI "WINDOWTITLE eq RSSHub" /F 2>nul && echo [OK] 已停止 RSSHub || echo [!] RSSHub 未运行

echo.
echo 所有组件已停止。
pause
