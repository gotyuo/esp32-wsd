// ============================================================
// 传感器采集 ESP8266 v6.1.3
// AHT20 + BMP280 共用 D1/D2 硬件 Wire 400kHz
// MAX30102 独立 D3/D4 硬件 Wire
//   MAX30102=0x57
//   注：当前调试期优先驱动 MAX30102；AHT20/BMP280 仍保留接口
// ============================================================
#include "sensors.h"
#include "max30102.h"
#include "pins.h"
#include <Wire.h>
#include "aht20.h"
#include "bmp280.h"
#include "max30102.h"

static AHT20    aht;
static BMP280   bmp;
static MAX30102 max30;

bool SensorHub::begin() {
    // 参考 max30102-esp8266-fw：100k 起步、重试、自动交换 SDA/SCL
    int sda = PIN_MAX30102_SDA;
    int scl = PIN_MAX30102_SCL;
    bool ok = false;
    for (int pass = 0; pass < 2 && !ok; pass++) {
        if (pass) {
            delay(200);
            int t = sda;
            sda = scl;
            scl = t;
        }

        Wire.begin(sda, scl);
        Wire.setClock(100000);
        delay(50);

        Serial.printf("[I2C] probe sda=%d scl=%d\n", sda, scl);
        {
            uint8_t found = 0;
            for (uint8_t a = 0x08; a < 0x78; a++) {
                Wire.beginTransmission(a);
                uint8_t rc = Wire.endTransmission();
                if (rc == 0) {
                    Serial.printf("[I2C] found 0x%02X\n", a);
                    found++;
                }
            }
            if (found == 0) Serial.println(F("[I2C] no ack on bus"));
        }

        for (int r = 0; r < 3 && !ok; r++) {
            if (r) delay(100);
            ok = max30.begin(&Wire);
        }
    }

    if (ok) {
        _max_ok = true;
        Serial.println(F("[SENSOR] MAX30102 OK"));
    } else {
        Serial.println(F("[SENSOR] MAX30102 not found"));
    }

    if (_max_ok) return true;

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

void SensorHub::peekStatus(uint8_t &val) { max30.peekStatus(val); }
void SensorHub::peekWrPtr(uint8_t &val) { max30.peekWrPtr(val); }
void SensorHub::peekRdPtr(uint8_t &val) { max30.peekRdPtr(val); }
bool SensorHub::peekFifoRaw(uint8_t *buf, uint8_t n) { return max30.peekFifoRaw(buf, n); }
uint16_t SensorHub::maxLastRed() const { return max30.lastRed(); }
uint16_t SensorHub::maxLastIR()  const { return max30.lastIR(); }
uint16_t SensorHub::maxPeakRed() const { return max30.peakRed(); }
uint16_t SensorHub::maxPeakIR()  const { return max30.peakIR(); }
