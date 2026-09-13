#pragma once
// ============================================================
// 引脚定义 (ESP8266 ESP-12F) — 用户实际接线
//
// I2C 总线 1 (GPIO12/14):
//   OLED SSD1306  0.96" 128x64  SCL=D5(GPIO14)  SDA=D6(GPIO12)
//   MAX30102                  SCL=D5(GPIO14)  SDA=D6(GPIO12)
//   (MAX30102 与 OLED 共用总线, 不同地址 0x3C/0x57)
//
// I2C 总线 2 (GPIO13/15):
//   AHT20 + BMP280            SCL=D8(GPIO15)  SDA=D7(GPIO13)
//
// ESP-12F 引脚映射:
//   D0=GPIO16  D1=GPIO5  D2=GPIO4  D3=GPIO0  D4=GPIO2
//   D5=GPIO14  D6=GPIO12 D7=GPIO13 D8=GPIO15
// ============================================================

// ---------- OLED SSD1306 (u8g2 软件 I2C) ----------
#define PIN_OLED_SDA 12       // D6 = GPIO12
#define PIN_OLED_SCL 14       // D5 = GPIO14
#define OLED_ADDR    0x3C

// ---------- MAX30102 (独立 I2C 总线) ----------
#define PIN_MAX30102_SDA 0        // D3 = GPIO0
#define PIN_MAX30102_SCL 2        // D4 = GPIO2

// ---------- AHT20 + BMP280 (硬件 Wire I2C) ----------
#define PIN_I2C_SDA 13        // D7 = GPIO13
#define PIN_I2C_SCL 15        // D8 = GPIO15

// ---------- 麦克风 ----------
#define PIN_MIC A0

// ---------- 报警 LED (无空闲 GPIO, 未接) ----------
#define PIN_LED_R 255
#define PIN_LED_G 255

// ---------- BOOT 键 (GPIO0 已被 MAX30102 SDA 占用, 禁用) ----------
#define PIN_BOOT_KEY 255

#define FW_VER FW_VERSION