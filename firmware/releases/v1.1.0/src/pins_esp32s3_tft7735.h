#pragma once
// ============================================================
// ESP32-S3 TFT7735 引脚配置 — 所有引脚独立，不共用总线
// ============================================================
//   I2C0 (GPIO8/9)     : AHT20 + BMP280
//   I2C1 (GPIO14/13)   : MAX30102 心率血氧（独立）
//   SPI  (12/11/10/7/6) : ST7735 TFT 软件SPI
//   ADC  (GPIO4)       : MIC 麦克风
//   LEDC (GPIO21)      : Speaker 喇叭
//   GPIO (15/16/17/18) : RGB LED + 蜂鸣器
// 避开 strapping(0/3/45/46)、Flash(26-32)、USB(19/20)、UART0(43/44)
// ============================================================

// ---------- I2C0: AHT20 + BMP280 ----------
#define PIN_I2C0_SDA 8
#define PIN_I2C0_SCL 9
#define PIN_I2C_SDA PIN_I2C0_SDA   // 兼容宏
#define PIN_I2C_SCL PIN_I2C0_SCL

// ---------- I2C1: MAX30102 (心率血氧，独立总线) ----------
#define PIN_I2C1_SDA 14
#define PIN_I2C1_SCL 13

// ---------- SPI: ST7735 0.96" 160x80 IPS TFT (软件SPI) ----------
#define PIN_TFT_SCK  12
#define PIN_TFT_MOSI 11
#define PIN_TFT_CS   10
#define PIN_TFT_DC   7
#define PIN_TFT_RST  6
#define PIN_TFT_BLK  5
#define PIN_TFT_BL   PIN_TFT_BLK   // 兼容宏

// ---------- MIC 麦克风 (ADC1_CH3) ----------
// 裸驻极体咪头: 正极->GPIO4, 负极->GND
// 必须外接 4.7kΩ 偏置电阻到 3.3V，否则 ADC 恒为 0
#define PIN_MIC 4

// ---------- Speaker 喇叭 (LEDC PWM, 独立于蜂鸣器) ----------
// 无源喇叭/蜂鸣器: + ->GPIO21, - ->GND
#define PIN_SPEAKER 21

// ---------- 报警 RGB LED (共阴) ----------
#define PIN_LED_R 15
#define PIN_LED_G 16
#define PIN_LED_B 17
#define PIN_LED 15  // 兼容宏

// ---------- 报警蜂鸣器 (LEDC PWM) ----------
#define PIN_BUZZER 18

// ---------- 固件版本 ----------
#ifndef FW_VERSION
#define FW_VERSION "1.1.0"
#endif
