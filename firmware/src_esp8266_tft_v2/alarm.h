#pragma once
// ============================================================
// 报警模块 — 阈值判定 + 状态输出
// ESP8266 无空闲 GPIO 接 LED/蜂鸣器, 仅输出报警级别
// ============================================================
#include <Arduino.h>
#include "sensors.h"
#include "config_store.h"

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
    AlarmLevel level() const { return _level; }

private:
    AlarmLevel _level = AL_NODATA;
};