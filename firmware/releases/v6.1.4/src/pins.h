#pragma once
// ============================================================
// v6.1 引脚定义 (ESP8266 ESP-12F)
//
// OLED 0.96" I2C (SSD1306):
//   SCL=D5(GPIO14)  SDA=D6(GPIO12)  VDD=3V3  VSS=GND
//
// AHT20 + BMP280 共用一条硬件 I2C 总线:
//   SCL=D1(GPIO5)  SDA=D2(GPIO4)  VDD=3V3  GND=GND
//   地址不冲突: AHT20=0x38, BMP280=0x76
//
// MAX30102 与 AHT20/BMP280 共用一条硬件 I2C 总线 (v6.1.4):
//   SCL=D1(GPIO5)  SDA=D2(GPIO4)
//   地址: MAX30102=0x57
//
//  !! v6.1.2 bug 修复: MAX30102 严禁接 D8(GPIO15)。
//  GPIO15 是 ESP8266 启动跳线引脚(boot strap), 启动时必须为低电平;
//  MAX30102 模块 I2C 上拉电阻会把 GPIO15 拉高, 导致上电/复位后无法
//  从 Flash 启动 -> 黑屏。D1/D2 无启动跳线功能, 安全。
//
// ESP-12F 引脚映射:
//   D0=GPIO16  D1=GPIO5  D2=GPIO4  D3=GPIO0  D4=GPIO2
//   D5=GPIO14  D6=GPIO12 D7=GPIO13 D8=GPIO15
// ============================================================

// ---------- OLED SSD1306 (u8g2 硬件 I2C) ----------
#define PIN_OLED_SDA 12       // D6 = GPIO12
#define PIN_OLED_SCL 14       // D5 = GPIO14
#define PIN_OLED_RST 255      // 未接 RST
#define OLED_ADDR    0x3C

// ---------- 传感器总线 (AHT20 + BMP280 共用) ----------
#define PIN_I2C_SDA  4       // D2 = GPIO4
#define PIN_I2C_SCL  5       // D1 = GPIO5

// ---------- MAX30102 (与 AHT20/BMP280 共用总线) ----------
#define PIN_MAX30102_SDA  4        // D2 = GPIO4 (与 AHT20/BMP280 共用总线)
#define PIN_MAX30102_SCL  5        // D1 = GPIO5 (与 AHT20/BMP280 共用总线)

// ---------- 麦克风 ----------
#define PIN_MIC A0

// ---------- 报警 LED (无空闲 GPIO, 未接) ----------
#define PIN_LED_R 255
#define PIN_LED_G 255

// ---------- BOOT 键 (禁用，避免误触发下载模式) ----------
#define PIN_BOOT_KEY 255

// ---------- 版本 ----------
#ifndef FW_VERSION
#define FW_VERSION "6.1.4"
#endif
#define FW_VER     FW_VERSION

// ---------- OLED 界面轮播 ----------
#define OLED_PAGE_COUNT     4
#define OLED_PAGE_INTERVAL  5000UL   // 每 5 秒自动切换

// ---------- 历史数据 ----------
#define HISTORY_INTERVAL_MS     5UL * 60UL * 1000UL   // 5 分钟
#define HISTORY_MAX_POINTS      12                     // 1 小时窗口
#define HISTORY_FILE            "/history.dat"
