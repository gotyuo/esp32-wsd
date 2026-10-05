#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ESP8266 烧录助手（NodeMCU / CH340，历史端口 COM6）
来源: max30102-esp8266-fw

用法:
  python flash_esp8266.py scan          # pin_scan 固件 (默认)
  python flash_esp8266.py audio         # audio_test 固件
  python flash_esp8266.py scan COM6     # 指定端口（COM6 为历史端口；若插到其它口请改）

注意：本机 ESP8266 没有 DTR/RTS 自动复位，必须手动进 download 模式：
  1) GPIO0 接 GND（拉低并保持）
  2) 给板子断电再上电（或点 RST），停在 download 模式
  3) 保持 GPIO0 接地，运行本脚本
  4) 连上即写入；完成后断开 GPIO0 与 GND，按 RST 运行

注意：ESP8266 这台不是 COM7（COM7 是 ESP32），它通常枚举在 COM6。
      若现在插在别的口，请用参数指定，例如 flash_esp8266.py scan COMx
"""
import os, sys, time, subprocess, serial.tools.list_ports

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\esp\Scripts\python.exe'
WINDOW = 150

BINS = {
    'scan':  os.path.join(HERE, 'firmware_pin_scan_2026-09-14.bin'),
    'audio': os.path.join(HERE, 'firmware_audio_test_2026-09-14.bin'),
}

def port_present(com):
    return any(p.device == com for p in serial.tools.list_ports.comports())

def main():
    which = 'scan'
    com = 'COM6'
    for a in sys.argv[1:]:
        if a.lower() in BINS:
            which = a.lower()
        elif a.upper().startswith('COM'):
            com = a.upper()
    BIN = BINS[which]
    if not os.path.exists(BIN):
        print('固件不存在:', BIN); return 2
    if not os.path.exists(VENV_PY):
        print('esptool 环境不存在:', VENV_PY); return 2
    print(f'目标: {com}  固件: {os.path.basename(BIN)}  ({os.path.getsize(BIN)//1024}KB)')
    print(f'烧录窗口 {WINDOW}s：GPIO0 接地 + ESP 干净上电，停在 download 模式')
    deadline = time.time() + WINDOW
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        if not port_present(com):
            print(f'[尝试 {attempt}] {com} 不在，等待端口出现...', flush=True)
            time.sleep(2); continue
        print(f'[尝试 {attempt}] 打开 {com} 连接 download 模式...', flush=True)
        r = subprocess.run(
            [VENV_PY, '-m', 'esptool', '--chip', 'esp8266', '-p', com, '-b', '115200',
             '--before', 'no_reset', '--connect-attempts', '2', '--after', 'no_reset',
             'write_flash', '0x0', BIN],
            capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode == 0:
            print('==== 烧录成功 ===='); print(r.stdout[-1800:]); return 0
        tail = (r.stderr or r.stdout).strip().splitlines()[-2:]
        print('  未连上:', ' | '.join(t.replace('\n', ' ') for t in tail)[-240:], flush=True)
        time.sleep(1)
    print('==== 未连上：检查 GPIO0 接地 / 干净上电 / CH340 TX->GPIO3 ====')
    return 1

if __name__ == '__main__':
    raise SystemExit(main())
