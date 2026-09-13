#pragma once
// ============================================================
// 引脚定义 (ESP8266 ESP-12F) — 6 线 SPI TFT 变体
//
// 屏幕：0.96" SPI TFT (ST7735S) 160x80, 走软件 SPI
//       (ESP8266 硬件 SPI 被 Flash 占用，只能用软件 SPI)
// 传感器：AHT20 + BMP280 走硬件 I2C(Wire)
//
// GPIO 预算：ESP8266 可用 GPIO 仅 7 个
//   不可用: GPIO0=BOOT / GPIO6-11=Flash / GPIO15=上电必须低
//   可用:   GPIO2,4,5,12,13,14,16 = 7 个
//
// 6 线 TFT 接法：
//   VSS->GND, VDD->3V3, CS->D0, DC->D1,
//   MOSI->D4, SCK->D2, BL->D5, RST->悬空
//   传感器 SDA->D6, SCL->D7
// ============================================================

// ---------- 4 线 SPI TFT 屏 (VCC/GND/MOSI/SCK) ----------
// 4 引脚屏幕: CS/DC/RST/BL 由模块内部处理, 不占用 GPIO
// 如使用 6 线屏(带 CS/DC/BL 引脚), 取消下方注释并改为:
//   #define PIN_TFT_CS   16
//   #define PIN_TFT_DC    5
//   #define PIN_TFT_BL   14
#define PIN_TFT_CS   255    // 4 引脚: 内部 (6 线屏改为 16/D0)
#define PIN_TFT_DC   255    // 4 引脚: 内部 (6 线屏改为 5/D1)
#define PIN_TFT_RST  255    // 不接 RST 引脚(模块内部上电自复位)
#define PIN_TFT_MOSI  2     // D4 = GPIO2
#define PIN_TFT_SCK   4     // D2 = GPIO4
#define PIN_TFT_BL   255    // 4 引脚: 内部常亮 (6 线屏改为 14/D5)

// ---------- 硬件 I2C: AHT20 + BMP280 (传感器) ----------
// Wire.begin(sda=12, scl=13)
#define PIN_I2C_SDA  12     // D6 = GPIO12
#define PIN_I2C_SCL  13     // D7 = GPIO13

// ---------- MAX30102 (血氧/脉率) ----------
// 注意：TFT 变体的 7 个可用 GPIO 已全部被占用
//   (GPIO16,5,2,4,14=TFT / GPIO12,13=传感器 I2C)，
//   没有空闲引脚接 MAX30102，故本变体不支持血氧/脉率。
// 如需血氧/脉率请使用 src_esp8266 (OLED-less) 或 ESP32 变体。

// ---------- 麦克风 ----------
#define PIN_MIC A0

// ---------- LED (ESP8266 无空闲 GPIO 接 LED) ----------
#define PIN_LED_R 255
#define PIN_LED_G 255

// ---------- 工厂按键 ----------
// ESP8266 无空闲 GPIO 接按键；factory reset 走串口命令 "factory"
#define PIN_BOOT_KEY 255

#define FW_VER FW_VERSION