#pragma once
// ============================================================
// 传感器采集模块 (ESP8266): AHT20 + BMP280
// 与 ESP32 版接口一致，但无体征 ADC（ESP8266 只有单通道 A0）
// ============================================================
#include <Arduino.h>

struct EnvData {
    float temp_c   = NAN;
    float hum_pct  = NAN;
    float pres_hpa = NAN;
    float sp_o2    = NAN;
    float pr_hr    = NAN;
    float ecg_hr   = NAN;
    float rr_bpm   = NAN;
    float glucose  = NAN;
    bool  valid    = false;
};

class SensorHub {
public:
    bool begin();
    bool read(EnvData &out);
    void readVitals(EnvData &out);
    bool aht_ok()  const { return _aht_ok; }
    bool bmp_ok()  const { return _bmp_ok; }

private:
    bool _aht_ok = false;
    bool _bmp_ok = false;
};
