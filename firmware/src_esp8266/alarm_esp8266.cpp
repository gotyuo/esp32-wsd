// ============================================================
// 报警模块 ESP8266 - RGB LED + 无源蜂鸣器
// 用 analogWrite/tone 代替 ESP32 的 ledc
// ============================================================
#include "alarm_esp8266.h"
#include "pins_esp8266.h"

#define NORMAL_PERIOD   3000
#define WARNING_PERIOD  900
#define ALARM_PERIOD    300
#define NODATA_PERIOD   1600

void AlarmDevice::begin() {
    pinMode(PIN_LED_R, OUTPUT);
    pinMode(PIN_LED_G, OUTPUT);
    setRGB(false, false);
}

void AlarmDevice::setRGB(bool r, bool g) {
    digitalWrite(PIN_LED_R, r ? HIGH : LOW);
    digitalWrite(PIN_LED_G, g ? HIGH : LOW);
}

void AlarmDevice::buzzerOn(uint32_t freq) { (void)freq; }  // 蜂鸣器已移除（D5 改作 I2C 时钟）
void AlarmDevice::buzzerOff() { }

static int band(float v, float lo, float hi) {
    if (isnan(v)) return 0;
    if (v < lo || v > hi) return 2;
    float span = hi - lo;
    float margin = span * 0.10f;
    if (v < lo + margin || v > hi - margin) return 1;
    return 0;
}

AlarmLevel AlarmDevice::evaluate(const EnvData &d, const DeviceConfig &cfg) {
    if (!d.valid) { _level = AL_NODATA; return _level; }
    if (!cfg.alarm_enabled) { _level = AL_NORMAL; return _level; }

    int worst = 0;
    worst = max(worst, band(d.temp_c,   cfg.temp_min, cfg.temp_max));
    worst = max(worst, band(d.hum_pct,  cfg.hum_min,  cfg.hum_max));
    worst = max(worst, band(d.pres_hpa, cfg.pres_min, cfg.pres_max));

    _level = (worst == 2) ? AL_ALARM : (worst == 1) ? AL_WARNING : AL_NORMAL;
    return _level;
}

void AlarmDevice::update(AlarmLevel level, bool alarm_sound) {
    uint32_t now = millis();
    switch (level) {
    case AL_NORMAL: {
        uint32_t ph = (now % NORMAL_PERIOD);
        bool on = (ph < NORMAL_PERIOD / 2);
        setRGB(false, on);
        buzzerOff();
        break;
    }
    case AL_WARNING: {
        if (now - _lastToggle >= WARNING_PERIOD / 2) {
            _lastToggle = now;
            _phase = !_phase;
        }
        setRGB(_phase, _phase);
        buzzerOff();
        break;
    }
    case AL_ALARM: {
        if (now - _lastToggle >= ALARM_PERIOD / 2) {
            _lastToggle = now;
            _phase = !_phase;
        }
        setRGB(_phase, false);
        if (alarm_sound) {
            if ((now % 1000) < 500) buzzerOn(2700);
            else buzzerOff();
        } else {
            buzzerOff();
        }
        break;
    }
    case AL_NODATA: {
        if (now - _lastToggle >= NODATA_PERIOD / 2) {
            _lastToggle = now;
            _phase = !_phase;
        }
        setRGB(false, false);
        buzzerOff();
        break;
    }
    case AL_CONFIG: {
        uint32_t period = NODATA_PERIOD;
        bool on = ((now % period) < period / 2);
        setRGB(false, on);
        buzzerOff();
        break;
    }
    }
}
