// ============================================================
// 传感器采集 ESP8266 v4.0
// 三颗传感器共用同一 I2C 总线 (SCL=D8/SDA=D7), 走硬件 Wire 400kHz
//   AHT20=0x38, BMP280=0x76, MAX30102=0x57 (地址不冲突)
// ============================================================
#include "sensors.h"
#include "pins.h"
#include <Wire.h>
#include "aht20.h"
#include "bmp280.h"
#include "max30102.h"

static AHT20    aht;
static BMP280   bmp;
static MAX30102 max30;

bool SensorHub::begin() {
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
    Wire.setClock(400000);
    delay(20);

    if (aht.begin(&Wire)) {
        _aht_ok = true;
        Serial.println(F("[SENSOR] AHT20 OK"));
    } else {
        Serial.println(F("[SENSOR] AHT20 not found"));
    }

    if (bmp.begin(&Wire)) {
        _bmp_ok = true;
        Serial.println(F("[SENSOR] BMP280 OK"));
    } else {
        Serial.println(F("[SENSOR] BMP280 not found"));
    }

    max30.setPins(PIN_I2C_SDA, PIN_I2C_SCL);
    if (max30.begin(&Wire)) {
        _max_ok = true;
        Serial.println(F("[SENSOR] MAX30102 OK"));
    } else {
        Serial.println(F("[SENSOR] MAX30102 not found"));
    }

    return _aht_ok || _bmp_ok || _max_ok;
}

bool SensorHub::read(EnvData &out) {
    bool got = false;
    float t, h, p;

    if (_aht_ok && aht.read(t, h)) {
        out.temp_c  = t;
        out.hum_pct = h;
        got = true;
    }
    if (_bmp_ok && bmp.read(t, p)) {
        out.pres_hpa = p;
        if (isnan(out.temp_c)) out.temp_c = t;
        got = true;
    }
    out.valid = got;
    return got;
}

void SensorHub::readVitals(EnvData &out) {
    out.sp_o2 = NAN;
    out.pr_hr = NAN;
    if (_max_ok) {
        float spo2, hr;
        if (max30.read(spo2, hr)) {
            out.sp_o2 = spo2;
            out.pr_hr = hr;
        }
    }
}
