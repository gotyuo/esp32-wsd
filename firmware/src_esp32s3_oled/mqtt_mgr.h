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
    // 应用服务器下发的 TTS 语音播报文本
    // JSON: {"text":"...","level":N,"device_id":"..."}
    void applyTtsPayload(const String &json);
    // 获取最近一次 TTS 播报文本（主循环读取后清空）
    // outLevel 输出报警级别 0=信息 1=预警 2=报警
    String takeTtsText(int *outLevel = nullptr);

private:
    void ensureConn();
    bool doConnect();

    uint32_t _lastTry = 0;
    uint32_t _retryDelay = 2000;

    // TTS 播报队列（简单单缓冲，主循环轮询）
    String _ttsText;
    int    _ttsLevel = 0;
    bool   _ttsPending = false;
};

extern MqttMgr g_mqtt;
