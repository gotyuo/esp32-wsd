// ============================================================
// 传感器采集 ESP8266 - AHT20 + BMP280 (复用 ESP32 版驱动)
// ============================================================
#include "sensors_esp8266.h"
#include "pins_esp8266_tft.h"
#include <Wire.h>
#include "aht20.h"
#include "bmp280.h"

static AHT20  aht;
static BMP280 bmp;

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
    return _aht_ok || _bmp_ok;
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
    // ESP8266 只有单通道 A0，不做多通道体征采集
    out.sp_o2  = NAN;
    out.pr_hr  = NAN;
    out.ecg_hr = NAN;
    out.rr_bpm = NAN;
    out.glucose = NAN;
}
