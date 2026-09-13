#pragma once
// ============================================================
// 报警模块 (ESP8266): RGB LED + 无源蜂鸣器
// ESP8266 用 analogWrite/tone 代替 ESP32 的 ledc
// ============================================================
#include <Arduino.h>
#include "sensors_esp8266.h"
#include "config_store_esp8266.h"

enum AlarmLevel : uint8_t {
    AL_NORMAL  = 0,
    AL_WARNING = 1,
    AL_ALARM   = 2,
    AL_NODATA  = 3,
    AL_CONFIG  = 4
};

class AlarmDevice {
public:
    void begin();
    AlarmLevel evaluate(const EnvData &d, const DeviceConfig &cfg);
    void update(AlarmLevel level, bool alarm_sound);
    AlarmLevel level() const { return _level; }

private:
    void setRGB(bool r, bool g);
    void buzzerOn(uint32_t freq);
    void buzzerOff();

    AlarmLevel _level = AL_NODATA;
    uint32_t   _lastToggle = 0;
    bool       _phase = false;
};
