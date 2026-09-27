#pragma once
// ============================================================
// AHT20 温湿度传感器驱动（I2C, 地址 0x38）
// 纯手写实现，无第三方依赖
// 量程: 温度 -40~85℃(±0.3℃)  湿度 0~100%RH(±2%)
// ============================================================
#include <Arduino.h>
#include <Wire.h>

#define AHT20_ADDR 0x38

class AHT20 {
public:
    bool begin(TwoWire *wire = &Wire);
    // 单次测量，成功返回 true
    bool read(float &temp_c, float &hum_pct);

private:
    bool sendCmd(const uint8_t *cmd, size_t len);
    bool triggerAndRead(uint8_t *buf);
    bool softReset();
    TwoWire *_wire = nullptr;
};
