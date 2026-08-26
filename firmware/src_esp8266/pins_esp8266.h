#pragma once
// ============================================================
// 引脚定义 (ESP8266 ESP-12F) - 实测接线 2026-08
//
// 实测扫描结果(I2C 探测):
//   OLED(0x3C):            SDA=GPIO2(D4)  SCL=GPIO14(D5)
//   AHT20(0x38)+BMP280(0x77): SDA=GPIO13(D7) SCL=GPIO12(D6)
//
// ESP8266 GPIO 限制：
//   - ADC 只有 A0（单通道 0-1V 或 0-3.3V）
//   - GPIO6~11 接 Flash，绝对不能用
//   - GPIO0(D3)/GPIO2(D4)/GPIO15(D8) 是 boot strap，上电电平有要求
//   - GPIO16(D0) 不能挂中断，不支持 PWM；作 GPIO 可用（boot 时高电平）
// ============================================================

// ---------- OLED: 0.96" SSD1306/SSD1315 (u8g2 库, 软件 I2C) ----------
// 实测: SDA=GPIO2(D4), SCL=GPIO14(D5), 地址 0x3C
#define PIN_OLED_SDA 2       // D4 = GPIO2
#define PIN_OLED_SCL 14      // D5 = GPIO14
#define OLED_ADDR    0x3C

// ---------- 硬件 I2C: AHT20 + BMP280 (传感器, Wire 引脚) ----------
// 实测: SDA=GPIO13(D7), SCL=GPIO12(D6)
#define PIN_I2C_SDA 13       // D7 = GPIO13
#define PIN_I2C_SCL 12       // D6 = GPIO12

// ---------- 麦克风 ----------
// 驻极体咪头接 A0（ESP8266 唯一 ADC 引脚，0-1V 范围）
#define PIN_MIC A0

// ---------- 报警 LED（摸底: 原 D6/D7 已被传感器占用, 暂不接） ----------
// LED 已放弃: D6/D7 让给传感器 I2C; ESP8266 无独立 GPIO 给 RGB
#define PIN_LED_R 255        // 未接
#define PIN_LED_G 255        // 未接

// ---------- RESET / BOOT 键 ----------
// GPIO0 = D3 = BOOT 键。上电按住 3 秒触发 factory reset,
// 重启后进默认 AP 模式(192.168.4.1), 换位置时恢复出厂即用。
#define PIN_BOOT_KEY 0

#define FW_VER FW_VERSION