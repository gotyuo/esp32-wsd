#include "alarm.h"
#include "pins.h"

// ============================================================
// LEDC API 兼容层
// ESP32 Arduino Core 2.x: 通道制 API (ledcSetup/ledcAttachPin/ledcWriteTone(ch,...))
// ESP32 Arduino Core 3.x: 引脚制 API (ledcAttach/ledcWriteTone(pin,...))
// Core 3.x 废弃了旧 API，编译能过但 ledcSetup/ledcAttachPin 不做事 → 无声
// ============================================================
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  // ---- Core 3.x: 引脚制 ----
  #define BUZZ_ATTACH(pin, freq, res)  ledcAttach(pin, freq, res)
  #define BUZZ_TONE(pin, freq)         ledcWriteTone(pin, freq)
  #define BUZZ_DUTY(pin, duty)         ledcWrite(pin, duty)
  #define BUZZ_DETACH(pin)             ledcDetach(pin)
#else
  // ---- Core 2.x: 通道制 ----
  #define BUZZ_CH 0
  #define BUZZ_ATTACH(pin, freq, res)  do { ledcSetup(BUZZ_CH, freq, res); ledcAttachPin(pin, BUZZ_CH); } while(0)
  #define BUZZ_TONE(pin, freq)         ledcWriteTone(BUZZ_CH, freq)
  #define BUZZ_DUTY(pin, duty)         ledcWrite(BUZZ_CH, duty)
  #define BUZZ_DETACH(pin)             ledcDetachPin(pin)
#endif

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
    // Core 3.x: ledcAttach(引脚, 频率, 分辨率位)
    // Core 2.x: ledcSetup(通道, 频率, 分辨率位) + ledcAttachPin(引脚, 通道)
    BUZZ_ATTACH(PIN_BUZZER, 2000, 8);
    buzzerOff();

    Serial.println(F("[ALARM] buzzer initialized"));
    // 测试鸣叫一声，确认硬件正常
    buzzerOn(2700);
    delay(80);
    buzzerOff();
}

void AlarmDevice::setRGB(bool r, bool g, bool b) {
    digitalWrite(PIN_LED_R, r ? HIGH : LOW);
    digitalWrite(PIN_LED_G, g ? HIGH : LOW);
    digitalWrite(PIN_LED_B, b ? HIGH : LOW);
}

void AlarmDevice::buzzerOn(uint32_t freq) {
    BUZZ_TONE(PIN_BUZZER, freq);
    BUZZ_DUTY(PIN_BUZZER, 128);   // 50% 占空比，最响
}

void AlarmDevice::buzzerOff() {
    BUZZ_DUTY(PIN_BUZZER, 0);
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

    // 体征阈值（固定医学正常范围）+ 突变检测
    worst = max(worst, band(d.sp_o2,   95, 100));
    worst = max(worst, band(d.pr_hr,   60, 100));
    worst = max(worst, band(d.ecg_hr,  60, 100));
    worst = max(worst, band(d.rr_bpm,  12, 25));
    worst = max(worst, band(d.glucose, 3.9, 6.1));

    // 心率突变检测：与上一次差值 >30% 视为突变
    static float s_prev_hr = NAN;
    if (!isnan(d.ecg_hr) && !isnan(s_prev_hr)) {
        float delta = fabsf(d.ecg_hr - s_prev_hr) / fmaxf(s_prev_hr, 1.0f);
        if (delta > 0.30f) worst = max(worst, 2);
    }
    if (!isnan(d.ecg_hr)) s_prev_hr = d.ecg_hr;

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

// ---------------- TTS 提示音 ----------------
// 播放短促提示音序列，表示收到 TTS 语音播报消息
// level: 0=信息(两短低音) 1=预警(三短中音) 2=报警(连续高音)
void playTtsAlert(int level) {
    // 直接用 LEDC 驱动 PIN_BUZZER，不经过 AlarmDevice（避免干扰报警状态机）
    const uint32_t freqs[] = {880, 1200, 2000};  // 低/中/高
    uint32_t freq = freqs[level > 2 ? 2 : level];

    if (level == 0) {
        // 信息：两短低音
        BUZZ_TONE(PIN_BUZZER, freq);
        BUZZ_DUTY(PIN_BUZZER, 128);
        delay(120);
        BUZZ_DUTY(PIN_BUZZER, 0);
        delay(80);
        BUZZ_TONE(PIN_BUZZER, freq);
        BUZZ_DUTY(PIN_BUZZER, 128);
        delay(120);
        BUZZ_DUTY(PIN_BUZZER, 0);
    } else if (level == 1) {
        // 预警：三短中音
        for (int i = 0; i < 3; i++) {
            BUZZ_TONE(PIN_BUZZER, freq);
            BUZZ_DUTY(PIN_BUZZER, 128);
            delay(100);
            BUZZ_DUTY(PIN_BUZZER, 0);
            delay(60);
        }
    } else {
        // 报警：连续高音 500ms
        BUZZ_TONE(PIN_BUZZER, freq);
        BUZZ_DUTY(PIN_BUZZER, 128);
        delay(500);
        BUZZ_DUTY(PIN_BUZZER, 0);
    }
}
