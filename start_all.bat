@echo off
chcp 65001 >nul
title Tentacle HRV 启动器

echo ==========================================
echo   Tentacle HRV 一键启动
echo ==========================================
echo.

REM ================================================================
REM  凭据（访问口令、隧道账号）不再写在本文件里 —— 本文件是公开仓库的一部分。
REM  它们统一放在 .env（已被 .gitignore 忽略）中，本脚本从这里读取：
REM      ACCESS_KEY=你的访问口令
REM      TUNNEL_SSH=用户名@隧道主机 -p 端口      （可选，不填则只在本机跑）
REM      PUBLIC_URL=https://你的固定域名          （可选，仅用于打印提示）
REM ================================================================
set "ACCESS_KEY="
set "TUNNEL_SSH="
set "PUBLIC_URL="
if exist "%~dp0.env" for /f "usebackq tokens=1,* delims==" %%a in ("%~dp0.env") do (
    if /i "%%a"=="ACCESS_KEY" set "ACCESS_KEY=%%b"
    if /i "%%a"=="TUNNEL_SSH" set "TUNNEL_SSH=%%b"
    if /i "%%a"=="PUBLIC_URL" set "PUBLIC_URL=%%b"
)

REM 检查 Python 是否可用
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装并加入环境变量。
    pause
    exit /b 1
)

if not defined ACCESS_KEY (
    echo [错误] .env 里没有配置 ACCESS_KEY。
    echo         请在 .env 中加一行：ACCESS_KEY=你的访问口令
    echo         然后用同一个口令访问控制页（?key=你的访问口令）。
    pause
    exit /b 1
)

REM 启动 Flask 后端
echo [1/2] 正在启动 Flask 后端...
start "Flask后端" cmd /k "cd /d %~dp0 && python server.py --port 8080"

REM 等待 2 秒，确保后端先启动
timeout /t 2 /nobreak >nul

REM 启动内网穿透（未配置 TUNNEL_SSH 则跳过）
if not defined TUNNEL_SSH (
    echo [2/2] 未配置 TUNNEL_SSH，跳过内网穿透（仅本机可访问）。
    goto :summary
)

where ssh >nul 2>nul
if errorlevel 1 (
    echo [2/2] [警告] 未找到 SSH 客户端，跳过内网穿透（仅本机可访问）。
    goto :summary
)

echo [2/2] 正在启动内网穿透...
REM 注意：这里不再使用 -o StrictHostKeyChecking=no（关闭主机密钥校验会带来中间人风险）。
REM 首次连接会提示确认主机指纹，输入 yes 即可；之后不再询问。
start "内网穿透" cmd /k "ssh -o ServerAliveInterval=30 -N -R 0:127.0.0.1:8080 %TUNNEL_SSH%"

:summary
echo.
echo 启动完成！
echo.
if defined PUBLIC_URL echo 平板访问地址: %PUBLIC_URL%/?key=%ACCESS_KEY%
echo 本机访问地址: http://127.0.0.1:8080/?key=%ACCESS_KEY%
echo.
echo 请保持新开窗口不要关闭；如需停止，直接关闭对应窗口。
echo.
pause
