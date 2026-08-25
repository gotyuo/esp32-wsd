#pragma once
// ============================================================
// 引脚定义 (ESP32-S3) — 4 孔 I2C OLED 变体
//
// 屏幕：0.96" I2C OLED (SSD1306/SSD1315, 4 孔, 地址 0x3C)
//       走独立的第二路 I2C(I2C1)，与传感器 I2C0 完全分离。
// 传感器：AHT20 + BMP280 走 I2C0(GPIO8/9)，与 OLED 不共用引脚。
// LED/蜂鸣/喇叭/麦克风：沿用主版 pins.h 分配。
//
// 4 孔接线：VSS->GND, VDD->3V3, SCL->GPIO13, SDA->GPIO14
// 地址 0x3C，与 AHT20(0x38)/BMP280(0x76) 不冲突（不同 I2C 总线）。
//
// ※ 6 线 SPI TFT 屏请使用 esp32-s3 主版（ST7735, CS=10/DC=7/RST=6/MOSI=11/SCK=12/BL=5）。
//    本变体仅含 OLED。
// ============================================================

// ---------- I2C0: AHT20 + BMP280 ----------
#define PIN_I2C_SDA 8
#define PIN_I2C_SCL 9

// ---------- I2C1: 0.96" OLED 屏幕 (4 孔 SSD1306/SSD1315) ----------
// 与传感器 I2C0 完全独立的第二路 I2C
#define PIN_OLED_SDA 14     // OLED SDA -> GPIO14
#define PIN_OLED_SCL 13     // OLED SCL -> GPIO13

// ---------- 麦克风 + 喇叭 ----------
#define PIN_MIC 4           // ADC1_CH3
#define PIN_SPEAKER 21

// ---------- 报警输出 ----------
// 共阴 RGB LED
#define PIN_LED_R 15
#define PIN_LED_G 16
#define PIN_LED_B 17
// 无源蜂鸣器
#define PIN_BUZZER 18

#define FW_VER FW_VERSION
