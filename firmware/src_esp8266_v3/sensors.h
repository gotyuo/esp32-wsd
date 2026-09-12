#pragma once
// ============================================================
// 传感器采集模块 (ESP8266): AHT20 + BMP280 (v3.0 无 MAX30102)
// ============================================================
#include <Arduino.h>

struct EnvData {
    float temp_c   = NAN;
    float hum_pct  = NAN;
    float pres_hpa = NAN;
    bool  valid    = false;
};

class SensorHub {
public:
    bool begin();
    bool read(EnvData &out);
    bool aht_ok() const { return _aht_ok; }
    bool bmp_ok() const { return _bmp_ok; }

private:
    bool _aht_ok = false;
    bool _bmp_ok = false;
};