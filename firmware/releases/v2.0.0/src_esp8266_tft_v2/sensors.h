#pragma once
// ============================================================
// 传感器采集 — AHT20 + BMP280 + MAX30102
// 三个共用 I2C 总线 (GPIO12/13), 不同地址
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
    bool read(EnvData &out);
    void readVitals(EnvData &out);
    bool aht_ok()  const { return _aht_ok; }
    bool bmp_ok()  const { return _bmp_ok; }
    bool max_ok()  const { return _max_ok; }

private:
    bool _aht_ok = false;
    bool _bmp_ok = false;
    bool _max_ok = false;
};