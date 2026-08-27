# ESP8266 显示模块接线说明（已更新 2026-08-27）

> 本仓库 ESP8266 现有 **两个变体**，固件与接线各不相同，请先看清自己手头板子再选。

## 变体一：ESP8266 + 0.96" I2C OLED（4 引脚）

- 固件环境：`esp8266`（PlatformIO env）
- 固件文件：`firmware/firmware_bin/envmon_esp8266.bin`（342,720 B）
- 屏幕：0.96" I2C OLED，SSD1306 / SSD1315，128×64

### 引脚对应关系（软件 I2C，与传感器 I2C 完全分离）

| OLED 引脚 | 接 ESP8266 引脚 | 备注 |
|-----------|------------------|------|
| VSS | GND | 共地 |
| VDD | 3V3 | 3.3V 供电，勿接 5V |
| SCL | D0 (GPIO16) | **软件 I2C，与传感器总线独立** |
| SDA | D1 (GPIO5)  | **软件 I2C，与传感器总线独立** |

### 关键说明
- **OLED 走软件 I2C (D0/D1)**，AHT20/BMP280 走硬件 I2C (D6/D7)，两路物理隔离、互不干扰
- I2C 地址：0x3C（默认）
- 缺屏时固件自动跳过，系统仍正常运行
- **不要使用旧版 D1/GPIO4 引脚（已废弃，与 Flash 冲突）**

### 接线示意
```
ESP8266 (ESP-12F)
┌─────────────┐
│ 3V3 ────────┼── VDD  (OLED)
│ GND ────────┼── VSS  (OLED)
│ D0  ────────┼── SCL  (OLED, 软件 I2C)
│ D1  ────────┼── SDA  (OLED, 软件 I2C)
│ D6  ────────┼── SCL  (AHT20 + BMP280, 硬件 I2C)
│ D7  ────────┼── SDA  (AHT20 + BMP280, 硬件 I2C)
└─────────────┘
```

## 变体二：ESP8266 + 1.8" SPI TFT LCD（6 引脚）

- 固件环境：`esp8266-tft`
- 固件文件：`firmware/firmware_bin/envmon_esp8266_tft.bin`（338,464 B）
- 屏幕：1.8" SPI TFT，ST7735S，128×160，触屏

### 引脚对应关系（软件 SPI）

| TFT 引脚 | 接 ESP8266 引脚 | 备注 |
|-----------|------------------|------|
| VCC | 3V3 | 3.3V |
| GND | GND | 共地 |
| CS  | D0 (GPIO16) | SPI 片选 |
| DC  | D1 (GPIO5)  | 数据/命令 |
| SDA/MOSI | D4 (GPIO2) | SPI 数据 |
| SCL/SCK | D2 (GPIO4) | SPI 时钟 |
| BLK | D5 (GPIO14) | 背光 |
| RST | 悬空 | 由固件软件复位 |

传感器仍走硬件 I2C：SDA=D6 (GPIO12)，SCL=D7 (GPIO13)。

### 接线示意
```
ESP8266 (ESP-12F)
┌─────────────┐
│ 3V3 ────────┼── VCC  (TFT)
│ GND ────────┼── GND  (TFT)
│ D0  ────────┼── CS   (TFT)
│ D1  ────────┼── DC   (TFT)
│ D4  ────────┼── SDA  (TFT SPI MOSI)
│ D2  ────────┼── SCL  (TFT SPI SCK)
│ D5  ────────┼── BLK  (TFT 背光)
│ D6  ────────┼── SCL  (AHT20 + BMP280)
│ D7  ────────┼── SDA  (AHT20 + BMP280)
└─────────────┘
```

## 烧录命令

### ESP8266 + I2C OLED
```bash
esptool.py --chip esp8266 --port /dev/ttyUSB0 --baud 460800 \
    erase_flash
esptool.py --chip esp8266 --port /dev/ttyUSB0 --baud 460800 \
    write_flash 0x00000 firmware/firmware_bin/envmon_esp8266.bin
```

### ESP8266 + SPI TFT
```bash
esptool.py --chip esp8266 --port /dev/ttyUSB0 --baud 460800 \
    erase_flash
esptool.py --chip esp8266 --port /dev/ttyUSB0 --baud 460800 \
    write_flash 0x00000 firmware/firmware_bin/envmon_esp8266_tft.bin
```

## 重新编译
```bash
cd firmware
sudo -E pio run -e esp8266        # I2C OLED 版
sudo -E pio run -e esp8266-tft    # SPI TFT 版
```

## 故障排查
- **屏不亮**：确认 3V3 供电；SPI 版检查 BLK 引脚是否接 D5
- **显示乱码**：检查 SCL/SDA 或 CS/DC 是否反接
- **OLED 无响应**：先用 I2C 扫描探针实测真实引脚再改代码，不要信文档默认值
- **软件 I2C 慢**：属正常，OLED 刷新周期 500ms 无感

## 参考
- ESP8266 OLED 主程序：`firmware/src_esp8266/main_esp8266.cpp`
- ESP8266 TFT 主程序：`firmware/src_esp8266_tft/main_esp8266_tft.cpp`
- 引脚常量：`pins_esp8266.h` / `pins_esp8266_tft.h`