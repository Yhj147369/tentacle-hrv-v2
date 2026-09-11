@echo off
chcp 65001 >nul
title Tentacle HRV 启动器
pushd "%~dp0"

echo ==========================================
echo   Tentacle HRV 一键启动
echo ==========================================
echo.

REM ================================================================
REM  凭据（访问口令、隧道账号）不在本文件里 —— 本文件属于公开仓库。
REM  它们统一放在 .env（已被 .gitignore 忽略），本脚本从这里读取：
REM      ACCESS_KEY=你的访问口令
REM      TUNNEL_SSH=用户名@隧道主机 -p 端口    （可选，不填则只在本机跑）
REM      PUBLIC_URL=https://你的固定域名        （可选，仅用于打印提示）
REM  本文件必须保存为 CRLF 行尾 + UTF-8(无 BOM)，否则 cmd 解析会出错（见 .gitattributes）。
REM ================================================================
set "ACCESS_KEY="
set "TUNNEL_SSH="
set "PUBLIC_URL="
if exist ".env" for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if /i "%%a"=="ACCESS_KEY" set "ACCESS_KEY=%%b"
    if /i "%%a"=="TUNNEL_SSH" set "TUNNEL_SSH=%%b"
    if /i "%%a"=="PUBLIC_URL" set "PUBLIC_URL=%%b"
)

REM 优先使用项目自带虚拟环境（依赖都装在 venv 里），用相对路径避免引号嵌套问题
set "PYRUN="
if exist "venv\Scripts\python.exe" set "PYRUN=venv\Scripts\python.exe"
if not defined PYRUN (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 venv\Scripts\python.exe，也找不到 python，请先安装 Python。
        popd
        pause
        exit /b 1
    )
    set "PYRUN=python"
)
echo 使用解释器: %PYRUN%
echo.

REM 端口占用检查：直接说清原因，避免 Flask 抛一大段 traceback 让人看不懂
netstat -ano | findstr /r /c:"TCP.*:8080 .*LISTENING" >nul
if not errorlevel 1 (
    echo [错误] 端口 8080 已被占用：已经有一个后端在跑了。
    echo         请先关闭旧的「Flask后端」窗口再运行本脚本；
    echo         若只是想打开页面，直接用下面的地址。
    echo.
    if defined ACCESS_KEY echo   本机: http://127.0.0.1:8080/?key=%ACCESS_KEY%
    popd
    pause
    exit /b 1
)

if not defined ACCESS_KEY (
    echo [提示] .env 未配置 ACCESS_KEY：后端会自动生成随机口令，
    echo         并打印在「Flask后端」窗口日志里（[安全] 开头那行）。
    echo.
)

echo [1/3] 启动 Flask 后端（独立窗口，请勿关闭）...
start "Flask后端" cmd /k "%PYRUN% server.py --port 8080"

REM 启动自检：等一会儿确认 8080 真的被监听（否则立刻告诉用户，而不是让人干等）
timeout /t 5 /nobreak >nul
netstat -ano | findstr /r /c:"TCP.*:8080 .*LISTENING" >nul
if errorlevel 1 (
    echo       [警告] 8080 还没进入监听：后端可能正在加载 Vosk 模型（约 30~60 秒），
    echo              也可能启动失败 —— 请看「Flask后端」窗口里的报错。
) else (
    echo       [OK] 8080 已监听。
)

set "DO_TUNNEL=0"
if defined TUNNEL_SSH (
    where ssh >nul 2>nul
    if not errorlevel 1 set "DO_TUNNEL=1"
)
if "%DO_TUNNEL%"=="1" (
    echo [2/3] 启动内网穿透（独立窗口，请勿关闭）...
    start "内网穿透" cmd /k "ssh -o ServerAliveInterval=30 -N -R 0:127.0.0.1:8080 %TUNNEL_SSH%"
) else (
    echo [2/3] 跳过内网穿透（未配置 TUNNEL_SSH 或找不到 ssh）—— 仅本机可访问。
)

echo [3/3] 完成。
echo.
echo ==========================================
echo   后端要加载 Vosk 模型，约 30~60 秒后才响应请求
echo ==========================================
echo.
if defined ACCESS_KEY (
    if defined PUBLIC_URL echo   平板/手机: %PUBLIC_URL%/?key=%ACCESS_KEY%
    echo   本机电脑: http://127.0.0.1:8080/?key=%ACCESS_KEY%
) else (
    echo   访问地址: http://127.0.0.1:8080/   （口令见「Flask后端」窗口日志）
)
echo.
echo 停止服务：关闭「Flask后端」与「内网穿透」两个窗口即可。
echo.
popd
pause