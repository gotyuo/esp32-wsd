#pragma once
// ============================================================
// 报警模块 — 阈值判定
// ============================================================
#include <Arduino.h>
#include "sensors.h"
#include "config.h"

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