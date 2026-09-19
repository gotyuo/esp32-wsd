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
    pinMode(PIN_I2C_SDA, INPUT_PULLUP);
    pinMode(PIN_I2C_SCL, INPUT_PULLUP);
    delay(50);
    int sda0 = digitalRead(PIN_I2C_SDA);
    int scl0 = digitalRead(PIN_I2C_SCL);
    delay(10);
    int sda1 = analogRead(PIN_I2C_SDA);
    int scl1 = analogRead(PIN_I2C_SCL);
    Serial.printf("[I2C] SDA=%d SCL=%d digital=%d/%d analog=%d/%d\n",
                  PIN_I2C_SDA, PIN_I2C_SCL, sda0, scl0, sda1, scl1);

    Serial.printf("[I2C] scan start SDA=%d SCL=%d\n", PIN_I2C_SDA, PIN_I2C_SCL);
    uint8_t foundMask = 0;
    for (uint8_t addr = 1; addr < 127; addr++) {
        Wire.beginTransmission(addr);
        uint8_t err = Wire.endTransmission();
        if (err == 0) {
            Serial.printf("[I2C] addr 0x%02X ACK\n", addr);
            if (addr == 0x38) foundMask |= 1;
            if (addr == 0x57) foundMask |= 2;
            if (addr == 0x76) foundMask |= 4;
        }
    }
    Serial.printf("[I2C] scan done mask=%d (aht=%d max=%d bmp=%d)\n",
                  foundMask, !!(foundMask & 1), !!(foundMask & 2), !!(foundMask & 4));

    Wire.setClock(100000);
    delay(30);
    Wire.setClock(400000);

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
        float t;
        if (max30.readTempC(t)) {
            out.max_temp_c = t;
        }
    }
}
