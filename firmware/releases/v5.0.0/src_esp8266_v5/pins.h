#pragma once
// ============================================================
// v4.0 引脚定义 (ESP8266 ESP-12F)
//
// OLED 0.96" I2C (SSD1306):
//   SCL=D5(GPIO14)  SDA=D6(GPIO12)  VDD=3V3  VSS=GND
//
// AHT20 + BMP280 + MAX30102 (共用同一 I2C 总线):
//   SCL=D8(GPIO15)  SDA=D7(GPIO13)  VDD=3V3  GND=GND
//   地址不冲突: AHT20=0x38, BMP280=0x76, MAX30102=0x57
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

// ---------- 传感器总线 (AHT20 + BMP280 + MAX30102 共用) ----------
#define PIN_I2C_SDA  13       // D7 = GPIO13
#define PIN_I2C_SCL  15       // D8 = GPIO15
#define PIN_MAX30102_SDA PIN_I2C_SDA
#define PIN_MAX30102_SCL PIN_I2C_SCL

// ---------- 麦克风 ----------
#define PIN_MIC A0

// ---------- 报警 LED (无空闲 GPIO, 未接) ----------
#define PIN_LED_R 255
#define PIN_LED_G 255

// ---------- BOOT 键 (禁用，避免误触发下载模式) ----------
#define PIN_BOOT_KEY 255

// ---------- 版本 ----------
#define FW_VERSION "5.0.0"
#define FW_VER     FW_VERSION

// ---------- OLED 界面轮播 ----------
#define OLED_PAGE_COUNT     4
#define OLED_PAGE_INTERVAL  5000UL   // 每 5 秒自动切换

// ---------- 历史数据 ----------
#define HISTORY_INTERVAL_MS     5UL * 60UL * 1000UL   // 5 分钟
#define HISTORY_MAX_POINTS      12                     // 1 小时窗口
#define HISTORY_FILE            "/history.dat"
