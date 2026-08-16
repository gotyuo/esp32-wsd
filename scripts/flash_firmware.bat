@echo off
REM ============================================================
REM  ESP32-S3 固件烧录脚本（Windows）
REM  用法: 双击运行，或 flash_firmware.bat COM6
REM  无需 PlatformIO，仅需 Python: pip install esptool
REM ============================================================
setlocal
set PORT=%1
if "%PORT%"=="" set PORT=COM6

echo [1/2] 烧录完整固件镜像到 %PORT% ...
python -m esptool --chip esp32s3 --port %PORT% --baud 460800 ^
    write_flash -z 0x0 "%~dp0..\firmware\release\envmon-v1.2.0-full.bin"
if errorlevel 1 (
    echo.
    echo 烧录失败。请检查:
    echo   1. 设备是否已连接并枚举串口（设备管理器查看 COM 号）
    echo   2. 若提示连接失败: 按住 BOOT 键再按一下 RST 键进入下载模式
    echo   3. 更换 USB 数据线（部分线材仅充电）
    pause
    exit /b 1
)
echo [2/2] 完成！设备将自动启动，首次运行进入配网模式。
pause
