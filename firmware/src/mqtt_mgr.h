#pragma once
// ============================================================
// MQTT 通信模块（使用内置 MqttClient，无第三方依赖）
// ============================================================
#include <Arduino.h>
#include "sensors.h"
#include "config_store.h"

class MqttMgr {
public:
    void begin();
    void loop();
    bool connected() const;
    // 发布一条遥测数据
    bool publishTelemetry(const EnvData &d, int alarm_level);
    bool publishVitals(const EnvData &d);
    // 应用服务器下发的配置 JSON（由接收回调调用）
    void applyConfigPayload(const String &json);

private:
    void ensureConn();
    bool doConnect();

    uint32_t _lastTry = 0;
    uint32_t _retryDelay = 2000;
};

extern MqttMgr g_mqtt;
