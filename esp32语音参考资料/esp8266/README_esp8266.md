ESP8266 烧录说明（COM6，非 COM7）
==============================

固件来源: max30102-esp8266-fw
已编译固件（两个测试/工具）:
  - firmware_pin_scan_2026-09-14.bin   (299KB)  引脚扫描 + 440Hz 测试音
  - firmware_audio_test_2026-09-14.bin (301KB)  I2S 音频测试
主固件源码: max30102_esp8266.ino（Web UI/MAX30102/HL7/波形，尚未编译，无 build/ 产物）

【进入 download 模式（无自动复位）】
  1) GPIO0 接 GND（拉低并保持）
  2) 给板子断电再上电（或点 RST），停在 download 模式
  3) 保持 GPIO0 接地，运行脚本
  4) 连上即写入；完成后断开 GPIO0 与 GND，按 RST 运行

【烧录】
  python flash_esp8266.py scan            # 默认 pin_scan，端口 COM6
  python flash_esp8266.py audio           # audio_test
  python flash_esp8266.py scan COMx       # 若插在别的口，指定端口

【端口说明】
  ESP8266 这台历史在 COM6（CH340）。COM7 是 ESP32，不要混用。
  若现在 ESP8266 插在别的口，请用参数指定对应 COM。

【若失败】
  检查 GPIO0 是否真接地、ESP 是否干净上电、CH340 的 TX->ESP GPIO3 是否接好。
