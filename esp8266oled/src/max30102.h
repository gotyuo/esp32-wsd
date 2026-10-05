#pragma once
// ============================================================
// MAX30102 血氧/心率传感器驱动（I2C，地址 0x57）
// 纯手写实现，无第三方依赖
// 接法: VCC->3V3, GND->GND, SDA->独立引脚, SCL->独立引脚
//        (与 AHT20/BMP280 不再共用同一组 SDA/SCL)
// 输出: sp_o2(血氧%) + pr_hr(脉率 bpm)，通过内部 4s 窗口(50Hz)计算
// 量程: SpO2 70~100%   心率 30~220 bpm
// ============================================================
#include <Arduino.h>
#include <Wire.h>

#define MAX30102_ADDR 0x57

class MAX30102 {
public:
    bool begin(TwoWire *wire = &Wire);
    // 设置 I2C 引脚（仅 ESP8266 等单总线芯片需要时分复用）
    void setPins(int sda, int scl) { _sda = sda; _scl = scl; }
    // 采集一次血氧/心率。返回 true = 有结果。
    // 内部以 50Hz 连续采样一个 4s 窗口；窗口未满则沿用上次结果
    bool read(float &sp_o2, float &hr_bpm);

private:
    bool     writeReg(uint8_t addr, uint8_t val);
    bool     readReg(uint8_t addr, uint8_t &val);
    bool     readFIFO(uint8_t *buf, uint8_t n);
    bool     readRegN(uint8_t addr, uint8_t *buf, uint8_t n);
    bool     writeTail(uint8_t tail);
    // ESP8266 单总线时分复用：每次 I2C 操作前切到目标引脚
    void     _ensureBus();

    static const int BUF = 200;          // 50Hz × 4s
    uint16_t _irBuf[BUF];
    uint16_t _redBuf[BUF];
    int      _write = 0;
    bool     _full  = false;
    uint16_t _lastRed = 0;
    uint16_t _lastIR = 0;

public:
    uint16_t lastRed() const { return _lastRed; }
    uint16_t lastIR() const { return _lastIR; }
    uint16_t peakRed() const {
        uint16_t mn = 65535, mx = 0;
        for (int i = 0; i < BUF; i++) { if (_redBuf[i] < mn) mn = _redBuf[i]; if (_redBuf[i] > mx) mx = _redBuf[i]; }
        return (mx > mn) ? (mx - mn) : 0;
    }
    uint16_t peakIR() const {
        uint16_t mn = 65535, mx = 0;
        for (int i = 0; i < BUF; i++) { if (_irBuf[i] < mn) mn = _irBuf[i]; if (_irBuf[i] > mx) mx = _irBuf[i]; }
        return (mx > mn) ? (mx - mn) : 0;
    }

    void peekStatus(uint8_t &val) { val = 0; readReg(0x00, val); }
    void peekWrPtr(uint8_t &val) { val = 0; readReg(0x04, val); }
    void peekRdPtr(uint8_t &val) { val = 0; readReg(0x06, val); }
    bool peekFifoRaw(uint8_t *buf, uint8_t n) { return readFIFO(buf, n); }

private:
    TwoWire *_wire = nullptr;
    int      _sda  = -1;
    int      _scl  = -1;
};
