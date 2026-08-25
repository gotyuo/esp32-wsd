#pragma once
// ============================================================
// 引脚定义 (ESP8266 ESP-12F)
//
// ESP8266 GPIO 限制：
//   - ADC 只有 A0（单通道 0-1V 或 0-3.3V），无法做多通道体征采集
//   - GPIO6~11 接 Flash，绝对不能用
//   - GPIO0(D3)/GPIO2(D4)/GPIO15(D8) 是 boot strap，上电电平有要求
//   - GPIO16(D0) 不能挂中断，且不支持 PWM/I2C
//   - 仅 GPIO2 / GPIO4 / GPIO5 支持 I2C；屏幕与传感器分 I2C 总线见下
//
// 可用引脚分配（屏幕/传感器共用一组 I2C，LED 独立一组；ESP8266 仅 1 个 I2C 外设）：
//   I2C(传感器+屏幕):  D3=GPIO2(SDA)  D5=GPIO14(SCL)   ← AHT20 + BMP280 + 0.96" OLED 并联
//   LED:               D6=GPIO12(R)   D7=GPIO13(G)     ← 共阴 RGB（B 脚省略），独立一组
//   LED:          D6=GPIO12(R)   D7=GPIO13(G)     ← 共阴 RGB（B 脚省略）
//   声音:          A0            ← 驻极体咪头
// ※ 放弃蜂鸣器：D5(GPIO14) 改作 I2C 时钟；状态指示靠 RGB LED + OLED。
// ※ ESP8266 无空闲 SPI 引脚，不支持 6 线 SPI TFT 屏幕（硬件硬限制）。
//   声音: A0              ← 驻极体咪头（需分压到 0-1V）
//
// ※ ESP8266 没有足够 GPIO 驱动 TFT 屏幕，本版本无屏幕显示。
//    配网用 WiFi AP + Web 页面，状态指示靠 RGB LED 颜色。
// ============================================================

// ---------- I2C: AHT20 + BMP280 ----------
// AHT20  : VCC->3V3, GND->GND, SDA->D3(GPIO2), SCL->D5(GPIO14)
// BMP280 : VCC->3V3, GND->GND, SDA->D3, SCL->D5 (与 AHT20 / OLED 并联)
// OLED   : VDD->3V3, VSS->GND, SDA->D3, SCL->D5 (并联，地址 0x3C 不冲突)
#define PIN_I2C_SDA 2       // D3 = GPIO2 (传感器 + OLED 共用)
#define PIN_I2C_SCL 14      // D5 = GPIO14 (传感器 + OLED 共用)

// ---------- 麦克风 ----------
// 驻极体咪头接 A0（ESP8266 唯一 ADC 引脚，0-1V 范围）
// ※ 必须用电阻分压将 3.3V 信号限制到 1V 以下，否则 ADC 饱和
#define PIN_MIC A0

// ---------- 报警输出 ----------
// 共阴 RGB LED: 只有 R/G 两色（ESP8266 引脚紧张，省蓝色）
//   R -> D6(GPIO12), G -> D7(GPIO13), 公共阴极 -> GND
#define PIN_LED_R 12        // D6
#define PIN_LED_G 13        // D7

#define FW_VER FW_VERSION
