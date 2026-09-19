#pragma once
// ============================================================
// MAX30102 血氧/心率传感器驱动（I2C，地址 0x57）
// 纯手写实现，无第三方依赖
// 接法: VCC->3V3, GND->GND, SDA->独立引脚, SCL->独立引脚
//        (与 AHT20/BMP280 不再共用同一组 SDA/SCL)
// HR/SpO2: 移植自 hrcalc.py / Maxim algorithm（4s @25Hz 窗口，4点移动平均）
// 温度: MAX30102 die temperature ADC，仅用于补偿/诊断，不代表人体体温
// ============================================================
#include <Arduino.h>
#include <Wire.h>

#define MAX30102_ADDR 0x57
#define MAX30102_SAMPLE_FREQ 25
#define MAX30102_MA_SIZE 4
#define MAX30102_BUF 100

class MAX30102 {
public:
    bool begin(TwoWire *wire = &Wire);
    // 设置 I2C 引脚（仅 ESP8266 等单总线芯片需要时分复用）
    void setPins(int sda, int scl) { _sda = sda; _scl = scl; }
    // 采集一次血氧/心率。返回 true = 有结果。
    // 内部以 25Hz 连续采样一个 4s 窗口；窗口未满则沿用上次结果
    bool read(float &sp_o2, float &hr_bpm);
    // 读取 MAX30102 片内温度（die temp），用于补偿/诊断
    bool readTempC(float &temp_c);
    float lastTempC() const { return _tempC; }

private:
    bool     writeReg(uint8_t addr, uint8_t val);
    bool     readReg(uint8_t addr, uint8_t &val);
    uint32_t read32(uint8_t dataIndex);
    bool     writeTail(uint8_t tail);
    // ESP8266 单总线时分复用：每次 I2C 操作前切到目标引脚
    void     _ensureBus();

    static const int BUF = MAX30102_BUF;
    float    _irBuf[BUF];
    float    _redBuf[BUF];
    int      _write = 0;
    int      _fill  = 0;
    bool     _full  = false;

    int      _hr = -999;
    bool     _hrValid = false;
    float    _hrAvg = 0;
    bool     _hrAvgValid = false;

    int      _spo2Raw = -999;   // x100
    bool     _spo2Valid = false;
    float    _spo2Avg = 0;      // %
    bool     _spo2AvgValid = false;

    float    _tempC = NAN;
    uint32_t _lastTempMs = 0;
    int      _tempBad = 0;

    TwoWire *_wire = nullptr;
    int      _sda  = -1;
    int      _scl  = -1;
};
