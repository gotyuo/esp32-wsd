# 设备备份与烧录引脚说明

日期：2026-09-19

重要规则：
- 不同 ESP 设备的 OLED 类型、引脚、传感器接线均可能不同。
- 恢复/烧录时必须从对应 IP 的子文件夹读取固件，禁止跨设备混刷。
- 如果设备硬件改动过，请先核对本文件中的引脚定义再烧录。

设备目录：

- 172.22.22.164
  - 备份目录：/home/hotyuo/esp32-wsd/backups/2026-09-19/172.22.22.164/
  - 烧录说明：172.22.22.164_README.md
  - PlatformIO env：esp32-4oled
  - 源码目录：/home/hotyuo/esp32-wsd/firmware/src_esp32_4oled
  - 固件来源：/home/hotyuo/esp32-wsd/firmware/.pio/build/esp32-4oled

- 172.22.22.251
  - 备份目录：/home/hotyuo/esp32-wsd/backups/2026-09-19/172.22.22.251/
  - 烧录说明：172.22.22.251_README.md
  - PlatformIO env：esp8266oled
  - 固件来源：/home/hotyuo/esp32-wsd/esp8266oled/.pio/build/esp8266oled
  - 源码快照来源：/home/hotyuo/esp32-wsd/firmware/src_esp8266_4oled

- 172.22.22.63
  - 备份目录：/home/hotyuo/esp32-wsd/backups/2026-09-19/172.22.22.63/
  - 烧录说明：172.22.22.63_README.md
  - PlatformIO env：esp8266oled
  - 固件来源：/home/hotyuo/esp32-wsd/esp8266oled/.pio/build/esp8266oled
  - 源码快照来源：/home/hotyuo/esp32-wsd/firmware/src_esp8266_4oled

整包：
/home/hotyuo/esp32-wsd/backups/envmon-device-backups-2026-09-19.tar.gz

校验：
/home/hotyuo/esp32-wsd/backups/envmon-device-backups-2026-09-19.tar.gz.sha256


### MAX30102 供电注意（通用）
- MAX30102 模块 VIN 必须接 3.3V，不能直接接 5V。
- 若 VIN/GND 接错或电流过高，可能拉低 ESP 的 3V3，导致 OLED 黑屏或启动时屏幕不亮。
- 已确认案例：拔掉 MAX30102 后 OLED 正常，插上后黑屏，优先按供电/负载问题排查。
