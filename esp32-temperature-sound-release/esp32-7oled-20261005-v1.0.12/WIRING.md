# 引脚对应接线顺序 — ESP32-S3 ST7735 TFT7735

> 说明：本固件固件版本为 `20261005-v1.0.11`。  
> 接线时优先使用 3.3V 逻辑电平；不要直接给 ESP32-S3 GPIO 供 5V。

## 1. 电源 / GND

| 模块 | 接线 | 说明 |
|---|---|---|
| 所有传感器/模块 GND | 接 ESP32-S3 `GND` | 必须共地 |
| 所有 3.3V 模块 VCC/VIN | 接 ESP32-S3 `3V3` | 不要接 5V 到 GPIO |

## 2. ST7735 0.96" IPS TFT 屏幕

| 屏幕引脚 | ESP32-S3 GPIO | 说明 |
|---|---:|---|
| SCK | GPIO12 | SPI 时钟 |
| MOSI / DATA | GPIO11 | SPI 数据 |
| CS | GPIO10 | SPI 片选 |
| DC | GPIO7 | 数据/命令选择 |
| RES / RST | GPIO6 | 复位 |
| BLK / BL | GPIO5 | 背光 |
| VCC | 3V3 | 屏幕供电 |
| GND | GND | 地 |

## 3. 温湿度 + 气压传感器

| 传感器 | VCC | GND | SDA | SCL | 说明 |
|---|---:|---:|---:|---:|---|
| AHT20 | 3V3 | GND | GPIO8 | GPIO9 | 温湿度 |
| BMP280 | 3V3 | GND | GPIO8 | GPIO9 | 气压，和 AHT20 共用 I2C0 |

可选：如果模块没有板上上拉电阻，给 SDA/SCL 各加 4.7kΩ 上拉到 3V3。

## 4. MAX30102 心率血氧

| MAX30102 | ESP32-S3 GPIO | 说明 |
|---|---:|---|
| VCC | 3V3 | 供电 |
| GND | GND | 地 |
| SDN | GPIO14 | I2C1 SDA |
| SCL | GPIO13 | I2C1 SCL |

MAX30102 使用独立 I2C1，不和温湿度/气压共用。

## 5. 麦克风 MIC

| MIC | ESP32-S3 | 说明 |
|---|---:|---|
| 正极/信号 | GPIO4 | ADC1_CH3 |
| 负极 | GND | 地 |
| 偏置 | GPIO4 -> 4.7kΩ -> 3V3 | 必须加，否则 ADC 可能恒为 0 |

## 6. 声音 / 报警输出

| 输出 | ESP32-S3 GPIO | 说明 |
|---|---:|---|
| Speaker 喇叭 | GPIO21 | LEDC PWM，独立喇叭通道 |
| Buzzer 蜂鸣器 | GPIO18 | LEDC PWM |

无源喇叭/蜂鸣器：正极接 GPIO，负极接 GND。

## 7. RGB LED 报警灯

| LED 引脚 | ESP32-S3 GPIO | 说明 |
|---|---:|---|
| R 红 | GPIO15 | 共阴 LED |
| G 绿 | GPIO16 | 共阴 LED |
| B 蓝 | GPIO17 | 共阴 LED |
| LED 公共负极 | GND | 共阴 |

## 8. USB 串口 / 烧录

| USB 线 | ESP32-S3 | 说明 |
|---|---:|---|
| USB D- | GPIO19 | USB 数据负 |
| USB D+ | GPIO20 | USB 数据正 |
| USB VBUS | 5V | USB 供电 |
| USB GND | GND | USB 地 |

本机固件使用 UART0 串口日志：TX=GPIO43，RX=GPIO44。若烧录设备只显示 `/dev/ttyACM0`，通常通过 USB CDC/CH340 桥转串口。

## 9. 推荐接线顺序

1. 先接所有模块 GND 到 ESP32-S3 GND。
2. 接 3V3 电源，确认模块电源正常。
3. 接屏幕 SPI 与背光。
4. 接 I2C0：AHT20 + BMP280。
5. 接 I2C1：MAX30102。
6. 接 MIC。
7. 接 Speaker、Buzzer、RGB LED。
8. 最后接 USB，执行烧录脚本。
