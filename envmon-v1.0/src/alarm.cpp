#include "alarm.h"
#include "pins.h"

#define BUZZ_CH 0

// 呼吸/闪烁节奏（ms）
#define NORMAL_PERIOD   3000   // 绿色呼吸周期
#define WARNING_PERIOD  900    // 橙色闪烁周期
#define ALARM_PERIOD    300    // 红色快闪周期
#define NODATA_PERIOD   1600   // 蓝色慢闪周期

void AlarmDevice::begin() {
    pinMode(PIN_LED_R, OUTPUT);
    pinMode(PIN_LED_G, OUTPUT);
    pinMode(PIN_LED_B, OUTPUT);
    setRGB(false, false, false);
    // 无源蜂鸣器：LEDC 产生方波
    ledcSetup(BUZZ_CH, 2000, 8);
    ledcAttachPin(PIN_BUZZER, BUZZ_CH);
    buzzerOff();
}

void AlarmDevice::setRGB(bool r, bool g, bool b) {
    digitalWrite(PIN_LED_R, r ? HIGH : LOW);
    digitalWrite(PIN_LED_G, g ? HIGH : LOW);
    digitalWrite(PIN_LED_B, b ? HIGH : LOW);
}

void AlarmDevice::buzzerOn(uint32_t freq) {
    ledcWriteTone(BUZZ_CH, freq);
    ledcWrite(BUZZ_CH, 128);   // 50% 占空比，最响
}

void AlarmDevice::buzzerOff() {
    ledcWrite(BUZZ_CH, 0);
}

// 判定单个值是否超出/接近 [lo, hi]：
// 返回 0=正常 1=接近边界(预警) 2=越界(报警)
static int band(float v, float lo, float hi) {
    if (isnan(v)) return 0;
    if (v < lo || v > hi) return 2;
    float span = hi - lo;
    float margin = span * 0.10f;     // 距边界 10% 内视为预警
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
        // 绿色呼吸（用分段近似）
        uint32_t period = NORMAL_PERIOD;
        uint32_t ph = (now % period);
        bool on = (ph < period / 2);
        if (on) setRGB(false, true, false); else setRGB(false, false, false);
        buzzerOff();
        break;
    }
    case AL_WARNING: {
        if (now - _lastToggle >= WARNING_PERIOD / 2) {
            _lastToggle = now;
            _phase = !_phase;
        }
        setRGB(_phase, _phase, false);   // 红+绿 = 橙
        buzzerOff();
        break;
    }
    case AL_ALARM: {
        if (now - _lastToggle >= ALARM_PERIOD / 2) {
            _lastToggle = now;
            _phase = !_phase;
        }
        setRGB(_phase, false, false);    // 红色快闪
        if (alarm_sound) {
            // 间歇鸣叫：500ms 响 / 500ms 停
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
        setRGB(false, false, _phase);    // 蓝色慢闪：传感器异常
        buzzerOff();
        break;
    }
    case AL_CONFIG: {
        // 青色呼吸：配网模式
        uint32_t period = NODATA_PERIOD;
        bool on = ((now % period) < period / 2);
        setRGB(false, on, on);           // 绿+蓝 = 青
        buzzerOff();
        break;
    }
    }
}
