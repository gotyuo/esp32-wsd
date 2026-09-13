// ============================================================
// 传感器采集 ESP8266 - AHT20 + BMP280 + MAX30102
// ============================================================
#include "sensors_esp8266.h"
#include "pins_esp8266.h"
#include <Wire.h>
#include "aht20.h"
#include "bmp280.h"
#include "max30102.h"

static AHT20  aht;
static BMP280 bmp;
static MAX30102 max30;

bool SensorHub::begin() {
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
    Wire.setClock(400000);
    delay(50);

    if (aht.begin(&Wire)) {
        _aht_ok = true;
        Serial.println(F("[SENSOR] AHT20 OK"));
    } else {
        Serial.println(F("[SENSOR] AHT20 not found!"));
    }

    if (bmp.begin(&Wire)) {
        _bmp_ok = true;
        Serial.println(F("[SENSOR] BMP280 OK"));
    } else {
        Serial.println(F("[SENSOR] BMP280 not found!"));
    }

    // MAX30102：独立引脚，通过 setPins + _ensureBus 时分复用
    // 必须先 setPins 再 begin，begin 内部 _ensureBus 会切到 MAX30102 引脚探测
    max30.setPins(PIN_MAX30102_SDA, PIN_MAX30102_SCL);
    if (max30.begin(&Wire)) {
        _max_ok = true;
        Serial.println(F("[SENSOR] MAX30102 OK"));
    } else {
        Serial.println(F("[SENSOR] MAX30102 not found!"));
    }
    // 确保切回传感器总线
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
    Wire.setClock(400000);

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
    out.sp_o2   = NAN;
    out.pr_hr   = NAN;
    out.ecg_hr  = NAN;
    out.rr_bpm  = NAN;
    out.glucose = NAN;

    if (_max_ok) {
        float spo2, hr;
        if (max30.read(spo2, hr)) {
            out.sp_o2 = spo2;
            out.pr_hr = hr;
        }
    }

    // 读完后切回传感器总线，保证 AHT20/BMP280 后续正常
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
    Wire.setClock(400000);
}
