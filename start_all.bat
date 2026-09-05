@echo off
chcp 65001 >nul
title Tentacle HRV 启动器

echo ==========================================
echo   Tentacle HRV 一键启动
echo ==========================================
echo.

REM 检查 Python 是否可用
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装并加入环境变量。
    pause
    exit /b 1
)

REM 检查 SSH 是否可用
where ssh >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 SSH 客户端，请安装 OpenSSH。
    pause
    exit /b 1
)

REM 启动 Flask 后端
echo [1/2] 正在启动 Flask 后端...
start "Flask后端" cmd /k "cd /d %~dp0 && python server.py --port 8080"

REM 等待 2 秒，确保后端先启动
timeout /t 2 /nobreak >nul

REM 启动 i996 内网穿透
echo [2/2] 正在启动 i996 内网穿透...
start "i996隧道" cmd /k "ssh -o StrictHostKeyChecking=no -R 0:127.0.0.1:8080 ClothoUseServerInforaqo17875@v2.i996.me -p 8222"

echo.
echo 启动完成！
echo.
echo 平板访问地址: https://bz9w6k.i996.me/?key=123456
echo 本机访问地址: http://127.0.0.1:8080/?key=123456
echo.
echo 请保持两个新窗口不要关闭。
echo 如需停止，直接关闭对应窗口即可。
echo.
pause