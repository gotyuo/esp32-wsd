#pragma once
// ============================================================
// 传感器采集模块：AHT20 (温湿度) + BMP280 (温度/气压)
// 温度以 AHT20 为主，BMP280 作交叉校验；气压取自 BMP280
// ============================================================
#include <Arduino.h>

struct EnvData {
    float temp_c   = NAN;   // ℃
    float hum_pct  = NAN;   // %RH
    float pres_hpa = NAN;   // hPa
    // 体征（来自 ESP32 ADC 或其他源）
    float sp_o2    = NAN;   // 血氧 %
    float pr_hr    = NAN;   // 脉率 bpm
    float ecg_hr   = NAN;   // 心电图心率 bpm
    float rr_bpm   = NAN;   // 呼吸频率 rpm
    float glucose  = NAN;   // 血糖 mmol/L
    bool  valid    = false;
};

class SensorHub {
public:
    bool begin();
    // 读取一次数据，返回是否成功
    bool read(EnvData &out);
    void readVitals(EnvData &out);
    bool aht_ok()  const { return _aht_ok; }
    bool bmp_ok()  const { return _bmp_ok; }

private:
    bool _aht_ok = false;
    bool _bmp_ok = false;
};
