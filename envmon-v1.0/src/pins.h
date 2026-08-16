#pragma once
// ============================================================
// 引脚定义 (ESP32-S3)
// 说明:
//  - GPIO 0/3/45/46 为 ESP32-S3 strapping 引脚，已避开
//  - GPIO 26~32 连接 Flash、33~37 可能被 PSRAM 占用，已避开
//  - GPIO 19/20 为 USB D-/D+，43/44 为 UART0，已避开
//  - 一对一接线表见 docs/01-硬件接线.md
// ============================================================

// ---------- I2C: AHT20 + BMP280 ----------
// AHT20  : VCC->3V3, GND->GND, SDA->GPIO8, SCL->GPIO9
// BMP280 : VCC->3V3, GND->GND, SDA->GPIO8, SCL->GPIO9 (与 AHT20 并联)
#define PIN_I2C_SDA 8
#define PIN_I2C_SCL 9

// ---------- SPI: GC9109 0.96" 160x80 IPS TFT ----------
// 屏引脚: VCC->3V3, GND->GND
//         SCK->GPIO12, MOSI->GPIO11, RST->GPIO6, DC->GPIO7, CS->GPIO10, BLK->GPIO5
#define PIN_TFT_CS   10
#define PIN_TFT_DC   7
#define PIN_TFT_RST  6
#define PIN_TFT_MOSI 11
#define PIN_TFT_SCK  12
#define PIN_TFT_BL   5    // 背光

// ---------- 麦克风 + 喇叭 ----------
// 裸驻极体咪头: 正极->GPIO4, 负极->GND
//   ※ 必须在 GPIO4 与 3V3 之间接 4.7kΩ 偏置电阻，否则 ADC 恒为 0
#define PIN_MIC 4        // ADC1_CH3
// 无源喇叭: 正极->GPIO21, 负极->GND (PWM 驱动)
#define PIN_SPEAKER 21

// ---------- 报警输出 ----------
// 共阴 RGB LED: 阴(公共端)接 GND，阳极高电平点亮，各串 220Ω 限流
#define PIN_LED_R 15
#define PIN_LED_G 16
#define PIN_LED_B 17
// 无源蜂鸣器: + 接 GPIO18，- 接 GND（需 PWM 驱动发声）
#define PIN_BUZZER 18

// ---------- 其他 ----------
#define FW_VER FW_VERSION