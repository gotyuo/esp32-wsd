# ESP8266 0.96" OLED 屏幕 (SSD1306 / SSD1315) 接入说明

## 硬件选型
- **屏幕**:0.96" I2C OLED,驱动芯片 **SSD1306 或 SSD1315**(两者命令兼容,固件自动适配),分辨率 128×64
- **接口**:4 孔,I2C 通信(无需第三/四颗引脚)
- **供电**:3.3V(接 ESP8266 3V3 引脚)
- **I2C 地址**:0x3C(模块背面标 A0/GND 跳线默认即 0x3C;若接 A1 则 0x3D,固件默认 0x3C)
- 价格约 8–15 元/块,无需额外库

## 引脚对应关系 (4 孔 ↔ ESP8266)

| OLED 引脚 | 功能       | 接 ESP8266 引脚 | 备注                                   |
|-----------|------------|------------------|----------------------------------------|
| VSS       | 地         | GND              | 共地                                   |
| VDD       | 电源       | 3V3              | 3.3V(不要用 5V,可能烧屏)               |
| SCL       | I2C 时钟   | D1 (GPIO5)       | **与 AHT20 / BMP280 共用**,并联即可     |
| SDA       | I2C 数据   | D2 (GPIO4)       | **与 AHT20 / BMP280 共用**,并联即可     |

> **关键**:OLED 的 I2C 地址 0x3C,与 AHT20(0x38)、BMP280(0x76)完全不相冲突,
> 三块模块可直接并联在同一对 D1/D2 总线上,无需切换。总线速率 400kHz(快模式)。

## 接线示意图
```
ESP8266 (ESP-12F)
┌─────────────┐
│ 3V3 ────────┼── VDD  (OLED)        AHT20 VCC   BMP280 VCC
│ GND ────────┼── VSS  (OLED)        AHT20 GND   BMP280 GND
│ D1  ────────┼── SCL  (OLED)  ───── AHT20 SCL   BMP280 SCL
│ D2  ────────┼── SDA  (OLED)  ───── AHT20 SDA   BMP280 SDA
└─────────────┘
```
- 总线两端建议各接 4.7kΩ 上拉电阻到 3V3(多数模块板载了上拉,可省去)。
- 若屏幕接 A1 跳线(地址 0x3D),修改 `ssd1306.h` 的 `OLED_ADDR` 或 `begin()` 第三参即可。

## 烧录文件
固件烧录镜像保存在 `firmware/firmware_bin/` 目录:

| 文件名                          | 说明                                                        | 大小      |
|---------------------------------|-------------------------------------------------------------|-----------|
| envmon_esp8266.bin              | ESP8266 **标准版**(无屏幕,原固件)                           | 335,344 B |
| envmon_esp8266_oled.bin         | ESP8266 **带屏版**(含 0.96" OLED,缺屏自动跳过继续运行)     | 338,128 B |
| envmon_esp32s3.bin              | ESP32-S3 合并镜像(bootloader + partitions + app,0x0 起烧) | 950,352 B |
| envmon_esp32s3_flashdump.bin    | ESP32-S3 设备实测 flash 整片(4MB,0x0 起烧)                | 4,194,304 B |

> **说明**:带屏版固件在无 OLED 时仍能正常启动运行(屏幕检测失败只打印一条提示),
> 所以 ESP8266 不接屏用 `envmon_esp8266_oled.bin` 也不会出问题;但为节省空间,
> 纯环境监测(不接屏)推荐使用 `envmon_esp8266.bin`。

## 烧录方法

### 用 esptool.py (命令行,通用)
**ESP8266 带屏版**(0x00000 全镜像,免分区):
```bash
esptool.py --chip esp8266 --port /dev/ttyUSB0 --baud 460800 \
    write_flash 0x00000 firmware_bin/envmon_esp8266_oled.bin
```

**ESP32-S3**(需按住 BOOT 进下载模式):
```bash
# 合并版镜像
esptool.py --chip esp32s3 --port /dev/ttyACM0 --baud 400000 \
    write_flash 0x00000 firmware_bin/envmon_esp32s3.bin
# 或整片 flashdump
esptool.py --chip esp32s3 --port /dev/ttyACM0 --baud 400000 \
    write_flash 0x00000 firmware_bin/envmon_esp32s3_flashdump.bin
```
> ESP32-S3 进下载模式:按住 BOOT → 按 RESET → 松开 BOOT → 再执行烧录命令。

### 用 PlatformIO 重新编译(修改后)
```bash
cd firmware
sudo -E pio run -e esp8266          # 编译 ESP8266
sudo -E pio run -e esp32-s3          # 编译 ESP32-S3
sudo -E pio run -e esp8266 -t upload --upload-port /dev/ttyUSB0   # 烧录
```

## 上电显示内容
屏幕刷新周期 500ms,显示:
- 顶部:固件版本 + ESP8266 标识 + 右上 WiFi 信号格(0–4)
- 中部:温度 ℃ / 湿度 % / 气压 hPa(来自 AHT20 + BMP280)
- 底部:WiFi 状态 / MQTT 状态 / 报警等级(lvl 0=正常 1=告警 2=报警 3=无数据)

## 故障排查
- **屏不亮**:确认 VDD 接 3V3 非 5V;查 SCL/SDA 是否反接;I2C 地址是否 0x3C。
- **显示乱码/半屏**:芯片可能是 SSD1315,启动序列已兼容;若仍异常检查对比度 0xCF。
- **三传感器都读不到**:优先排查 D1/D2 上拉与供电,地址 0x3C/0x38/0x76 用 I2C Scanner 扫一次。
- 缺屏时用 `envmon_esp8266.bin`(无屏版),系统运行不受影响。
