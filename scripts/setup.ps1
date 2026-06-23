# QQ_Monitor_Bot 环境初始化脚本 (PowerShell)
# 用法: 右键 "以 PowerShell 运行"

Write-Host "QQ_Monitor_Bot 环境初始化" -ForegroundColor Cyan
Write-Host "========================`n" -ForegroundColor Cyan

# 1. 创建虚拟环境
Write-Host "[1/3] 创建 Python 虚拟环境..." -ForegroundColor Yellow
$venvPath = Join-Path $PSScriptRoot "..\.venv"
python -m venv $venvPath
Write-Host "  OK" -ForegroundColor Green

# 2. 安装依赖
Write-Host "[2/3] 安装 Python 依赖..." -ForegroundColor Yellow
$pip = Join-Path $venvPath "Scripts\pip.exe"
& $pip install --upgrade pip -q
& $pip install -r (Join-Path $PSScriptRoot "..\requirements.txt") -q
Write-Host "  OK" -ForegroundColor Green

# 3. 创建 data 目录
Write-Host "[3/3] 创建数据目录..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "..\data\logs") | Out-Null
Write-Host "  OK" -ForegroundColor Green

Write-Host "`n初始化完成！" -ForegroundColor Cyan
Write-Host "下一步：安装 NapCatQQ 并启动" -ForegroundColor Cyan
