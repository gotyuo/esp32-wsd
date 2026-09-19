#pragma once
// ============================================================
// MAX30102 血氧/心率传感器驱动（I2C，地址 0x57）
// 纯手写实现，无第三方依赖
// 接法: VCC->3V3, GND->GND, SDA->独立引脚, SCL->独立引脚
//        (与 AHT20/BMP280 不再共用同一组 SDA/SCL)
// 输出: sp_o2(血氧%) + pr_hr(脉率 bpm)，通过内部 4s 窗口计算
// 量程: SpO2 70~100%   心率 30~220 bpm
// ============================================================
#include <Arduino.h>
#include <Wire.h>

#define MAX30102_ADDR 0x57

class MAX30102 {
public:
    bool begin(TwoWire *wire = &Wire);
    void setPins(int sda, int scl) { _sda = sda; _scl = scl; }
    bool read(float &sp_o2, float &hr_bpm);

private:
    bool     writeReg(uint8_t addr, uint8_t val);
    bool     readReg(uint8_t addr, uint8_t &val);
    bool     readFifo(uint8_t *buf, uint8_t n);
    void     processSample(float red, float ir);
    void     _ensureBus();

    static const int BUF = 100;          // 约 4s @ 25Hz
    float    _irBuf[BUF];
    float    _redBuf[BUF];
    int      _write = 0;
    bool     _full  = false;

    float    _dcEstIR = 0;
    float    _lp1 = 0, _lp2 = 0;
    float    _peakEnv = 0;
    bool     _aboveThr = false;
    uint32_t _lastBeatMs = 0;
    uint32_t _ibis[7];
    uint8_t  _ibiCount = 0;
    int      _hr = 0;
    bool     _hrValid = false;
    float    _irDcSlow = 0;
    bool     _finger = false;
    uint32_t _fingerOffMs = 0;

    int      _spo2 = 0;
    bool     _spo2Valid = false;
    float    _spo2Avg = 0;
    uint8_t  _badRatio = 0;
    uint32_t _spo2Counter = 0;
    int      _spo2Fill = 0;
    int      _spo2Idx = 0;

    TwoWire *_wire = nullptr;
    int      _sda  = -1;
    int      _scl  = -1;
};
