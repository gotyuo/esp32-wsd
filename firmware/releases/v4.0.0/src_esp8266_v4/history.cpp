// ============================================================
// 历史数据缓存 ESP8266 - 纯内存环形缓冲
// ============================================================
#include "history.h"
#include "pins.h"

void HistoryStore::begin() {
    _count = 0;
    Serial.printf("[HIST] ring buffer init: %d points, interval=%dms\n",
                  HISTORY_MAX_POINTS, (int)(HISTORY_INTERVAL_MS / 1000));
}

void HistoryStore::record(const EnvData &d) {
    // 新点插到队尾; 超出容量丢弃最旧
    if (_count < HISTORY_MAX_POINTS) {
        HistoryPoint &p = _points[_count++];
        p.ts = millis();
        p.temp_c   = d.temp_c;
        p.hum_pct  = d.hum_pct;
        p.pres_hpa = d.pres_hpa;
        p.sp_o2    = d.sp_o2;
        p.pr_hr    = d.pr_hr;
    } else {
        // 左移一个，写入新点
        for (uint8_t i = 0; i + 1 < HISTORY_MAX_POINTS; i++) _points[i] = _points[i + 1];
        HistoryPoint &p = _points[HISTORY_MAX_POINTS - 1];
        p.ts = millis();
        p.temp_c   = d.temp_c;
        p.hum_pct  = d.hum_pct;
        p.pres_hpa = d.pres_hpa;
        p.sp_o2    = d.sp_o2;
        p.pr_hr    = d.pr_hr;
    }
    Serial.printf("[HIST] record #%d: t=%.1f h=%.1f p=%.1f spo2=%.1f hr=%.1f\n",
                  _count, d.temp_c, d.hum_pct, d.pres_hpa, d.sp_o2, d.pr_hr);
}

String HistoryStore::toJson() const {
    String out = "[";
    for (uint8_t i = 0; i < _count; i++) {
        if (i) out += ",";
        const HistoryPoint &p = _points[i];
        // ts 单位 ms, 前端可格式化
        out += "{"
               "\"ts\":"      + String((long)p.ts) + ","
               "\"temp\":"    + String(p.temp_c, 1) + ","
               "\"hum\":"     + String(p.hum_pct, 1) + ","
               "\"pres\":"    + String((int)p.pres_hpa) + ","
               "\"spo2\":"    + String(p.sp_o2, 1) + ","
               "\"hr\":"      + String(p.pr_hr, 1) +
               "}";
    }
    out += "]";
    return out;
}
