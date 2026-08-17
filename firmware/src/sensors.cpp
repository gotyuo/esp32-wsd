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
        if (isnan(out.temp_c)) out.temp_c = t;
        got = true;
    }

    out.valid = got;
    return got;
}

// ---------- 体征 ADC 读取（需信号调理） ----------
void SensorHub::readVitals(EnvData &out) {
    out.sp_o2  = NAN;
    out.pr_hr  = NAN;
    out.ecg_hr = NAN;
    out.rr_bpm = NAN;
    out.glucose = NAN;

    #ifdef PIN_VITAL_ECG
        analogSetPinAttenuation(PIN_VITAL_ECG, ADC_11db);
        int32_t s1 = 0;
        for (int i = 0; i < 32; i++) s1 += analogRead(PIN_VITAL_ECG);
        out.ecg_hr = (float)(s1 / 32) / 30.0f;
    #endif
    #ifdef PIN_VITAL_PULSE
        analogSetPinAttenuation(PIN_VITAL_PULSE, ADC_11db);
        int32_t s2 = 0;
        for (int i = 0; i < 32; i++) s2 += analogRead(PIN_VITAL_PULSE);
        out.pr_hr = (float)(s2 / 32) / 30.0f;
    #endif
    #ifdef PIN_VITAL_BREATH
        analogSetPinAttenuation(PIN_VITAL_BREATH, ADC_11db);
        int32_t s3 = 0;
        for (int i = 0; i < 32; i++) s3 += analogRead(PIN_VITAL_BREATH);
        out.rr_bpm = (float)(s3 / 32) / 200.0f;
    #endif
}
