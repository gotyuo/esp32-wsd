#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ESP32-S3 (TFT7735 固件 v1.2.0) 烧录助手 —— 目标 COM7（原生 USB CDC，USBSER）
来源: icu/esp32-wsd/firmware/releases/v1.2.0

烧录偏移（与该版本官方 flash.sh / README 一致）:
  bootloader.bin  @ 0x00000000
  partitions.bin  @ 0x00008000
  firmware.bin    @ 0x00010000

进入 download 模式（ESP32-S3 原生 USB 版）:
  1) 按住 BOOT 键（GPIO0）
  2) 点一下 RESET 键
  3) 松开 BOOT 键
  —— 芯片停在 download 模式，本脚本在窗口期内反复尝试连接

用法:
  python flash_com7_esp32.py
"""
import os, sys, time, subprocess, serial.tools.list_ports

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\esp\Scripts\python.exe'
COM = 'COM7'
BAUD = 921600
WINDOW = 120  # 秒

FILES = [
    (0x00000000, os.path.join(HERE, 'bootloader.bin')),
    (0x00008000, os.path.join(HERE, 'partitions.bin')),
    (0x00010000, os.path.join(HERE, 'firmware.bin')),
]

def port_present():
    return any(p.device == COM for p in serial.tools.list_ports.comports())

def main():
    for off, fp in FILES:
        if not os.path.exists(fp):
            print('缺少固件文件:', fp)
            return 2
    if not os.path.exists(VENV_PY):
        print('esptool 环境不存在:', VENV_PY)
        return 2
    print(f'目标: {COM}  固件: ESP32-S3 envmon v1.2.0 (TFT7735)')
    print(f'烧录窗口 {WINDOW}s：请按住 BOOT -> 点 RESET -> 松开 BOOT，让它停在 download 模式')
    deadline = time.time() + WINDOW
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        if not port_present():
            print(f'[尝试 {attempt}] {COM} 不在，等待端口出现...', flush=True)
            time.sleep(2)
            continue
        print(f'[尝试 {attempt}] 打开 {COM} 连接 download 模式...', flush=True)
        cmd = [VENV_PY, '-m', 'esptool', '--chip', 'esp32s3', '-p', COM, '-b', str(BAUD),
               'write_flash', '--flash_mode', 'dio', '--flash_freq', '40m', '--flash_size', 'detect',
               '--before', 'no_reset', '--after', 'no_reset']
        for off, fp in FILES:
            cmd += [hex(off), fp]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode == 0:
            print('==== 烧录成功 ====')
            print(r.stdout[-2000:])
            print('完成后按一下 RESET 键运行（原生 USB 版）。')
            return 0
        tail = (r.stderr or r.stdout).strip().splitlines()[-2:]
        print('  未连上:', ' | '.join(t.replace('\n', ' ') for t in tail)[-240:], flush=True)
        time.sleep(1)
    print('==== 窗口结束仍未连上：检查 BOOT/RESET 时序、USB 线、COM7 是否真的是该 ESP32-S3 ====')
    return 1

if __name__ == '__main__':
    raise SystemExit(main())
