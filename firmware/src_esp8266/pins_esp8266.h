#pragma once
// ============================================================
// 引脚定义 (ESP8266 ESP-12F)
//
// ESP8266 GPIO 限制：
//   - ADC 只有 A0（单通道 0-1V 或 0-3.3V），无法做多通道体征采集
//   - GPIO6~11 接 Flash，绝对不能用
//   - GPIO0(D3)/GPIO2(D4)/GPIO15(D8) 是 boot strap，上电电平有要求
//   - GPIO16(D0) 不能挂中断，且不支持 PWM/I2C
//
// 可用引脚分配：
//   I2C:  D1=GPIO5(SCL)  D2=GPIO4(SDA)   ← AHT20 + BMP280
//   LED:  D6=GPIO12(R)   D7=GPIO13(G)    ← 共阴 RGB（B 脚不接，ESP8266 引脚不够时省略蓝色）
//   蜂鸣: D5=GPIO14       ← 无源蜂鸣器
//   声音: A0              ← 驻极体咪头（需分压到 0-1V）
//
// ※ ESP8266 没有足够 GPIO 驱动 TFT 屏幕，本版本无屏幕显示。
//    配网用 WiFi AP + Web 页面，状态指示靠 RGB LED 颜色。
// ============================================================

// ---------- I2C: AHT20 + BMP280 ----------
// AHT20 : VCC->3V3, GND->GND, SDA->D2(GPIO4), SCL->D1(GPIO5)
// BMP280: VCC->3V3, GND->GND, SDA->D2, SCL->D1 (与 AHT20 并联)
#define PIN_I2C_SDA 4       // D2 = GPIO4
#define PIN_I2C_SCL 5       // D1 = GPIO5

// ---------- 麦克风 ----------
// 驻极体咪头接 A0（ESP8266 唯一 ADC 引脚，0-1V 范围）
// ※ 必须用电阻分压将 3.3V 信号限制到 1V 以下，否则 ADC 饱和
#define PIN_MIC A0

// ---------- 报警输出 ----------
// 共阴 RGB LED: 只有 R/G 两色（ESP8266 引脚紧张，省蓝色）
//   R -> D6(GPIO12), G -> D7(GPIO13), 公共阴极 -> GND
#define PIN_LED_R 12        // D6
#define PIN_LED_G 13        // D7
// 无源蜂鸣器: + 接 D5(GPIO14), - 接 GND
#define PIN_BUZZER 14       // D5

#define FW_VER FW_VERSION
