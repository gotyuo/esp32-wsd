#pragma once
// ============================================================
// 引脚定义 (ESP8266 ESP-12F)
//
// ESP8266 GPIO 限制：
//   - ADC 只有 A0（单通道 0-1V 或 0-3.3V），无法做多通道体征采集
//   - GPIO6~11 接 Flash，绝对不能用
//   - GPIO0(D3)/GPIO2(D4)/GPIO15(D8) 是 boot strap，上电电平有要求
//   - GPIO16(D0) 不能挂中断，且不支持 PWM/I2C
//   - 仅 GPIO2 / GPIO4 / GPIO5 支持硬件 I2C；传感器用硬件 I2C,
//     OLED 用软件模拟(bit-bang)I2C 占独立 GPIO,二者物理分离
//
// 可用引脚分配（三组完全独立,互不共享）：
//   硬件 I2C(传感器):   D4=GPIO2(SDA)   D5=GPIO14(SCL)   ← AHT20 + BMP280
//   软件 I2C(OLED):     D0=GPIO16(SDA)  D1=GPIO5(SCL)    ← 0.96" OLED(bit-bang,独立一组)
//   LED:                D6=GPIO12(R)    D7=GPIO13(G)     ← 共阴 RGB（B 脚省略），独立一组
//   声音:          A0            ← 驻极体咪头
// ※ 放弃蜂鸣器；状态指示靠 RGB LED + OLED。
// ※ ESP8266 无空闲 SPI 引脚，不支持 6 线 SPI TFT 屏幕（硬件硬限制）。
//   声音: A0              ← 驻极体咪头（需分压到 0-1V）
//
// ※ ESP8266 没有足够 GPIO 驱动 TFT 屏幕，本版本无屏幕显示。
//    配网用 WiFi AP + Web 页面，状态指示靠 RGB LED 颜色。
// ============================================================

// ---------- 硬件 I2C: AHT20 + BMP280 (传感器) ----------
// AHT20  : VCC->3V3, GND->GND, SDA->D4(GPIO2),  SCL->D5(GPIO14)
// BMP280 : VCC->3V3, GND->GND, SDA->D4, SCL->D5 (与 AHT20 并联,地址不冲突)
#define PIN_I2C_SDA 2       // D4 = GPIO2
#define PIN_I2C_SCL 14      // D5 = GPIO14

// ---------- 软件 I2C: 0.96" OLED (SSD1306/SSD1315, 独立一组 SDA/SCL) ----------
// OLED   : VDD->3V3, VSS->GND, SDA->D0(GPIO16), SCL->D1(GPIO5)
// 地址 0x3C,走 bit-bang 软件 I2C,与传感器硬件 I2C(D4/D5)物理上完全分开
#define PIN_OLED_SDA 16     // D0 = GPIO16
#define PIN_OLED_SCL 5      // D1 = GPIO5

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
