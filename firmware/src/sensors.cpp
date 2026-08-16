#include "sensors.h"
#include "pins.h"
#include <Wire.h>
#include "aht20.h"
#include "bmp280.h"

static AHT20  aht;
static BMP280 bmp;

bool SensorHub::begin() {
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000UL);
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
        // 若 AHT20 失效，用 BMP280 温度兜底
        if (isnan(out.temp_c)) out.temp_c = t;
        got = true;
    }

    out.valid = got;
    return got;
}
