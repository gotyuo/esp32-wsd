#pragma once
// ============================================================
// v5.0 引脚定义 (ESP8266 ESP-12F)
//
// 只保留 OLED 接线:
//   VDD = 3V3
//   VSS = GND
//   SCL = D5 (GPIO14)
//   SDA = D6 (GPIO12)
//
// ESP-12F 引脚映射:
//   D0=GPIO16  D1=GPIO5  D2=GPIO4  D3=GPIO0  D4=GPIO2
//   D5=GPIO14  D6=GPIO12 D7=GPIO13 D8=GPIO15
// ============================================================

#define PIN_OLED_SDA 12       // D6 = GPIO12
#define PIN_OLED_SCL 14       // D5 = GPIO14
#define OLED_ADDR    0x3C

#define FW_VERSION "5.0.0"
#define FW_VER FW_VERSION
