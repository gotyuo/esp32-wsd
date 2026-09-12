#pragma once
// ============================================================
// v3.0 引脚定义 (ESP8266 ESP-12F)
//
// OLED 0.96" I2C (SSD1306):
//   SCL=D5(GPIO14)  SDA=D6(GPIO12)  VDD=3V3  VSS=GND
// AHT20 + BMP280:
//   SCL=D8(GPIO15)  SDA=D7(GPIO13)  VDD=3V3  GND=GND
//   ⚠️ D8=GPIO15 上电必须为低电平，需外接上拉电阻
//
// ESP-12F 引脚映射:
//   D0=GPIO16  D1=GPIO5  D2=GPIO4  D3=GPIO0  D4=GPIO2
//   D5=GPIO14  D6=GPIO12 D7=GPIO13 D8=GPIO15
// ============================================================

// ---------- OLED SSD1306 (u8g2 软件 I2C) ----------
#define PIN_OLED_SDA 12       // D6 = GPIO12
#define PIN_OLED_SCL 14       // D5 = GPIO14
#define OLED_ADDR    0x3C

// ---------- AHT20 + BMP280 (硬件 Wire I2C) ----------
#define PIN_I2C_SDA 13        // D7 = GPIO13
#define PIN_I2C_SCL 15        // D8 = GPIO15

// ---------- 麦克风 ----------
#define PIN_MIC A0

// ---------- 报警 LED (无空闲 GPIO, 未接) ----------
#define PIN_LED_R 255
#define PIN_LED_G 255

// ---------- BOOT 键 (GPIO0 上电要求高电平, 避免误触发下载模式) ----------
#define PIN_BOOT_KEY 255

#define FW_VER FW_VERSION