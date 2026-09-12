#pragma once
// ============================================================
// MQTT 通信 — 基于 mqtt_client (WiFiClient 纯手写 MQTT 3.1.1)
// ============================================================
#include <Arduino.h>
#include "sensors.h"
#include "config_store.h"

class MqttMgr {
public:
    void begin();
    void loop();
    bool connected() const;
    bool publishTelemetry(const EnvData &d, int alarm_level);
    void applyConfig(const String &json);

private:
    bool doConnect();
    void ensureConn();
    uint32_t _lastTry = 0;
    uint32_t _retryDelay = 2000;
};

extern MqttMgr g_mqtt;