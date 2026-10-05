#pragma once
// ============================================================
// BMP280 气压/温度传感器驱动（I2C, 地址 0x76 或 0x77）
// 纯手写实现（数据手册补偿公式），无第三方依赖
// 量程: 气压 300~1100 hPa  温度 -40~85℃
// ============================================================
#include <Arduino.h>
#include <Wire.h>

class BMP280 {
public:
    bool begin(TwoWire *wire = &Wire);
    // 单次强制测量，成功返回 true
    bool read(float &temp_c, float &pres_hpa);

private:
    bool    readReg(uint8_t reg, uint8_t *buf, size_t len);
    bool    writeReg(uint8_t reg, uint8_t val);
    int16_t readS16(uint8_t reg);
    uint16_t readU16(uint8_t reg);
    int32_t read24(uint8_t reg);
    void    readCalibration();
    int32_t compTemp(int32_t adc_T);
    uint32_t compPres(int32_t adc_P);

    TwoWire *_wire = nullptr;
    uint8_t  _addr = 0x76;
    int32_t  _t_fine = 0;

    // 校准参数
    uint16_t _T1; int16_t _T2, _T3;
    uint16_t _P1; int16_t _P2, _P3, _P4, _P5, _P6, _P7, _P8, _P9;
};
