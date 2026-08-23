#pragma once
// ============================================================
// MQTT 通信模块 (ESP8266) - 复用 ESP32 版 mqtt_client
// ============================================================
#include <Arduino.h>
#include "sensors_esp8266.h"
#include "config_store_esp8266.h"

class MqttMgr {
public:
    void begin();
    void loop();
    bool connected() const;
    bool publishTelemetry(const EnvData &d, int alarm_level);
    bool publishVitals(const EnvData &d);
    void applyConfigPayload(const String &json);

private:
    void ensureConn();
    bool doConnect();

    uint32_t _lastTry = 0;
    uint32_t _retryDelay = 2000;
};

extern MqttMgr g_mqtt;
