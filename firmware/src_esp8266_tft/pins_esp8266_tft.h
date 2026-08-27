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

// ---------- 6 线 SPI TFT 屏 ----------
// 软件 SPI，引脚任意 GPIO(ESP8266 硬件 SPI 不可用)
// RST 引脚不占用——0.96" ST7735 模块内部 RST 有上拉+电容，
// 上电自动复位；如需要硬复位可软件 delay(200) 代替
#define PIN_TFT_CS   16     // D0 = GPIO16
#define PIN_TFT_DC    5     // D1 = GPIO5
#define PIN_TFT_RST 255     // 不接 RST 引脚(模块内部上电自复位)
#define PIN_TFT_MOSI  2     // D4 = GPIO2
#define PIN_TFT_SCK   4     // D2 = GPIO4
#define PIN_TFT_BL   14     // D5 = GPIO14

// ---------- 硬件 I2C: AHT20 + BMP280 (传感器) ----------
// Wire.begin(sda=12, scl=13)
#define PIN_I2C_SDA  12     // D6 = GPIO12
#define PIN_I2C_SCL  13     // D7 = GPIO13

// ---------- 麦克风 ----------
#define PIN_MIC A0

// ---------- LED (ESP8266 无空闲 GPIO 接 LED) ----------
#define PIN_LED_R 255
#define PIN_LED_G 255

// ---------- 工厂按键 ----------
// ESP8266 无空闲 GPIO 接按键；factory reset 走串口命令 "factory"
#define PIN_BOOT_KEY 255

#define FW_VER FW_VERSION