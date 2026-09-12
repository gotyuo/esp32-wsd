#pragma once
// ============================================================
// ESP8266 引脚定义 — 0.96" ST7735 TFT + 传感器
// ============================================================
// 屏幕: 0.96" SPI TFT (ST7735S) 160x80, 软件 SPI
//   ESP8266 硬件 SPI 被 Flash 占用, 只能用软件 SPI (bit-bang)
//
// 接线:
//   TFT:  VCC->3V3  GND->GND  SCK->GPIO4  MOSI->GPIO2
//         CS->GPIO16  DC->GPIO5  BL->GPIO14  RST->悬空(内部上电复位)
//   I2C:  SDA->GPIO12  SCL->GPIO13
//   传感器: AHT20(0x38) + BMP280(0x76/0x77) + MAX30102(0x57)
//           三个共用同一 I2C 总线, 不同地址
//
// 不可用 GPIO:
//   GPIO0  = BOOT 按钮
//   GPIO6-11 = Flash 内部
//   GPIO15 = 上电必须为低, 未引出
// ============================================================

// ---------- ST7735 TFT (软件 SPI, 6 引脚) ----------
#define PIN_TFT_CS   16
#define PIN_TFT_DC    5
#define PIN_TFT_RST  255    // 不接, 模块内部上电自复位
#define PIN_TFT_MOSI  2
#define PIN_TFT_SCK   4
#define PIN_TFT_BL   14

// ---------- I2C (硬件 Wire) ----------
#define PIN_I2C_SDA  12
#define PIN_I2C_SCL  13

// ---------- LED / 蜂鸣器 (ESP8266 无空闲 GPIO) ----------
#define PIN_LED_R 255
#define PIN_LED_G 255
#define PIN_BUZZER 255

// ---------- 固件版本 ----------
#define FW_VER FW_VERSION