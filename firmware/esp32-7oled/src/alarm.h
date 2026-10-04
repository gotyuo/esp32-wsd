#pragma once
// ============================================================
// 报警模块：共阴 RGB LED + 无源蜂鸣器
//  - 正常(NORMAL)  : 绿色慢速呼吸
//  - 预警(WARNING) : 橙色闪烁（接近阈值）
//  - 报警(ALARM)   : 红色快闪 + 蜂鸣器/喇叭鸣响（超出阈值）
//  全部为非阻塞实现，在 loop 中调用 update()
// ============================================================
#include <Arduino.h>
#include "sensors.h"
#include "config_store.h"

// TTS I2S 输出引脚（仅用于 ESP32-S3 下的 WAV 播放）
// 7oled 板：GPIO21 已用于无源喇叭 PWM，因此 I2S 输出必须避开它
#ifndef TTS_I2S_SDOUT_PIN
#define TTS_I2S_SDOUT_PIN 19
#endif
#ifndef TTS_I2S_BCLK_PIN
#define TTS_I2S_BCLK_PIN  18
#endif

enum AlarmLevel : uint8_t {
    AL_NORMAL  = 0,
    AL_WARNING = 1,
    AL_ALARM   = 2,
    AL_NODATA  = 3,  // 传感器无有效数据
    AL_CONFIG  = 4   // 配网 AP 模式
};

class AlarmDevice {
public:
    void begin();
    // 根据数据与阈值评估当前级别
    AlarmLevel evaluate(const EnvData &d, const DeviceConfig &cfg);
    // 非阻塞刷新 LED / 蜂鸣器 / 喇叭，millis 驱动
    void update(AlarmLevel level, bool alarm_sound);
    AlarmLevel level() const { return _level; }

private:
    void setRGB(bool r, bool g, bool b);
    void buzzerOn(uint32_t freq);
    void buzzerOff();
    void speakerOn(uint32_t freq);
    void speakerOff();

    AlarmLevel _level = AL_NODATA;
    uint32_t   _lastToggle = 0;
    bool       _phase = false;
    uint32_t   _beepUntil = 0;
};

// TTS 提示音播放函数（由主循环调用）
void playTtsAlert(int level);
void ttsStart(const String &url, const String &text = "");
void ttsStep();
bool ttsIsPlaying();
