#include "sensors.h"
#include "pins.h"
#include <Wire.h>
#include "aht20.h"
#include "bmp280.h"
#include "max30102.h"
#include "ad8232.h"

static AHT20    aht;
static BMP280   bmp;
static MAX30102 max30;
static AD8232   ecg;

// MAX30102 使用独立的 I2C1 总线 (GPIO14/13)，不与其他传感器共用
static TwoWire i2c1(1);

// MIC ADC 采样（GPIO4）
float SensorHub::readMic() {
    analogSetPinAttenuation(PIN_MIC, ADC_11db);
    int32_t sum = 0;
    for (int i = 0; i < 16; i++) sum += analogRead(PIN_MIC);
    return (float)(sum / 16) / 4095.0f;  // 归一化 0~1
}

bool SensorHub::begin() {
    // I2C0: AHT20 + BMP280 (GPIO8/9)
    Wire.begin(PIN_I2C0_SDA, PIN_I2C0_SCL, 400000UL);
    delay(50);

    if (aht.begin(&Wire)) { _aht_ok = true; Serial.println(F("[SENSOR] AHT20 OK")); }
    else { Serial.println(F("[SENSOR] AHT20 not found!")); }

    if (bmp.begin(&Wire)) { _bmp_ok = true; Serial.println(F("[SENSOR] BMP280 OK")); }
    else { Serial.println(F("[SENSOR] BMP280 not found!")); }

    // I2C1: MAX30102 (GPIO14/13，独立总线)
    i2c1.begin(PIN_I2C1_SDA, PIN_I2C1_SCL, 400000UL);
    delay(50);
    if (max30.begin(&i2c1)) { _max_ok = true; Serial.println(F("[SENSOR] MAX30102 OK (I2C1 GPIO14/13)")); }
    else { Serial.println(F("[SENSOR] MAX30102 not found")); }

    // MIC ADC
    analogSetPinAttenuation(PIN_MIC, ADC_11db);
    Serial.printf("[SENSOR] MIC ADC1_CH3 (GPIO%d) ready\n", PIN_MIC);

    #if defined(PIN_VITAL_ECG)
        if (ecg.begin(PIN_VITAL_ECG)) _ecg_ok = true;
        else Serial.println(F("[SENSOR] AD8232 unavailable"));
    #endif

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

// ---------- 体征采集（MAX30102 血氧/脉率 + AD8232 心电 + 备用呼吸 ADC） ----------
void SensorHub::readVitals(EnvData &out) {
    out.sp_o2   = NAN;
    out.pr_hr   = NAN;
    out.ecg_hr  = NAN;
    out.rr_bpm  = NAN;
    out.glucose = NAN;

    // MAX30102：血氧 %  + 脉率 bpm（I2C1 独立总线）
    if (_max_ok) {
        float spo2, hr;
        if (max30.read(spo2, hr)) {
            out.sp_o2 = spo2;
            out.pr_hr = hr;
        }
    }

    // AD8232：心电心率 bpm
    #ifdef PIN_VITAL_ECG
        float ecg_hr;
        if (ecg.read(ecg_hr)) {
            out.ecg_hr = ecg_hr;
        }
    #endif

    // 备用：裸 ADC 呼吸
    #ifdef PIN_VITAL_BREATH
        analogSetPinAttenuation(PIN_VITAL_BREATH, ADC_11db);
        int32_t s3 = 0;
        for (int i = 0; i < 32; i++) s3 += analogRead(PIN_VITAL_BREATH);
        out.rr_bpm = (float)(s3 / 32) / 200.0f;
    #endif
}
