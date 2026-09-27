#pragma once
// ============================================================
// 传感器采集 — AHT20 + BMP280 + MAX30102 (v4.0)
// ============================================================
#include <Arduino.h>

struct EnvData {
    float temp_c   = NAN;
    float hum_pct  = NAN;
    float pres_hpa = NAN;
    float sp_o2    = NAN;
    float pr_hr    = NAN;
    bool  valid    = false;
};

class SensorHub {
public:
    bool begin();
    bool read(EnvData &out);            // 环境数据 (AHT20 + BMP280)
    void readVitals(EnvData &out);      // 体征 (MAX30102)
    bool aht_ok()  const { return _aht_ok; }
    bool bmp_ok()  const { return _bmp_ok; }
    bool max_ok()  const { return _max_ok; }

private:
    bool _aht_ok = false;
    bool _bmp_ok = false;
    bool _max_ok = false;
};
