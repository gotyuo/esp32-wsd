#pragma once
// ============================================================
// 历史数据缓存 (ESP8266) - 纯内存环形缓冲
// 每 5 分钟存 1 个采样点，保留最近 HISTORY_MAX_POINTS 个
// 重启后历史丢失（如需持久化可改 LittleFS/SPIFFS）
// ============================================================
#include <Arduino.h>
#include "pins.h"
#include "sensors.h"

struct HistoryPoint {
    uint32_t ts;        // 采样时刻 millis()
    float    temp_c;
    float    hum_pct;
    float    pres_hpa;
    float    sp_o2;
    float    pr_hr;
};

class HistoryStore {
public:
    void begin();
    void record(const EnvData &d);
    uint8_t count() const { return _count; }
    const HistoryPoint &at(uint8_t i) const { return _points[i]; }

    // 返回 JSON 数组字符串 (供 Web 页面)
    String toJson() const;

    void clear() { _count = 0; }

private:
    HistoryPoint _points[HISTORY_MAX_POINTS] = {};
    uint8_t      _count = 0;
};
