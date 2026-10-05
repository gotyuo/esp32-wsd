@echo off
chcp 65001 >nul
REM ============================================================
REM  EnvMon beacon 服务端 — Windows 一键启动
REM  版本: 20261006-v1.1
REM  用法: 双击本文件，或在命令行 start-beacon.bat [出口网卡IP]
REM ============================================================

cd /d "%~dp0"

set IFACE=%1
if "%IFACE%"=="" (
    echo.
    echo 未指定出口网卡，使用系统默认路由。
    echo 若服务器有多张网卡（如同时有 192.168.x 和虚拟网卡），建议指定：
    echo     start-beacon.bat 192.168.2.218
    echo.
    echo 本机可用地址：
    ipconfig | findstr /i "IPv4"
    echo.
)

echo ============================================================
echo  EnvMon beacon 服务端  20261006-v1.1
echo ============================================================
echo  配置: projects.json
echo  出口: %IFACE%
echo  停止: Ctrl+C
echo ============================================================
echo.

if "%IFACE%"=="" (
    python beacon_server.py --config projects.json
) else (
    python beacon_server.py --config projects.json --interface %IFACE%
)

echo.
echo 服务端已退出。
pause
