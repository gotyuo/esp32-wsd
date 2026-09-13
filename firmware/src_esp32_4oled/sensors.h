#pragma once
// ============================================================
// 传感器采集模块：
//   - AHT20 (温湿度) + BMP280 (温度/气压)   I2C GPIO8/9
//   - MAX30102 (血氧 SpO2 / 脉率 HR)          I2C GPIO8/9 (并联)
//   - AD8232  (心电 ECG 心率)                 ADC GPIO1 (5V 供电)
// 温度以 AHT20 为主，BMP280 交叉校验；气压取自 BMP280
// ============================================================
#include <Arduino.h>

struct EnvData {
    float temp_c   = NAN;   // ℃
    float hum_pct  = NAN;   // %RH
    float pres_hpa = NAN;   // hPa
    // 体征（来自 MAX30102 / AD8232 / ESP32 ADC）
    float sp_o2    = NAN;   // 血氧 %
    float pr_hr    = NAN;   // 脉率 bpm (MAX30102)
    float ecg_hr   = NAN;   // 心电图心率 bpm (AD8232)
    float rr_bpm   = NAN;   // 呼吸频率 rpm
    float glucose  = NAN;   // 血糖 mmol/L
    bool  valid    = false;
};

class SensorHub {
public:
    bool begin();
    bool read(EnvData &out);
    void readVitals(EnvData &out);
    bool aht_ok()  const { return _aht_ok; }
    bool bmp_ok()  const { return _bmp_ok; }
    bool max_ok()  const { return _max_ok; }
    bool ecg_ok()  const { return _ecg_ok; }

private:
    bool _aht_ok = false;
    bool _bmp_ok = false;
    bool _max_ok = false;
    bool _ecg_ok = false;
};
