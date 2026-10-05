#pragma once
// ============================================================
// 轻量 MQTT 3.1.1 客户端（基于 WiFiClient，纯手写实现）
// 支持: CONNECT(含遗嘱)/CONNACK/PUBLISH(QoS0,1)/SUBSCRIBE/
//       PUBACK/PINGREQ 自动保活
// ============================================================
#include <Arduino.h>
#include <WiFiClient.h>
#include <functional>

#define MQTT_MAX_PACKET 768     // 收包缓冲
#define MQTT_MAX_TX     512     // 发包缓冲

typedef std::function<void(const char *topic, const uint8_t *payload, size_t len)> MqttMsgCb;

class MqttClient {
public:
    void setClient(WiFiClient *c) { _net = c; }
    void setCallback(MqttMsgCb cb) { _cb = cb; }

    // 连接服务器并建立 MQTT 会话
    bool connect(const char *host, uint16_t port, const char *clientId,
                 const char *user, const char *pass,
                 const char *willTopic = nullptr, const char *willMsg = nullptr,
                 uint16_t keepalive = 30);
    void disconnect();
    bool connected();

    bool publish(const char *topic, const char *payload, size_t len,
                 bool retain = false, uint8_t qos = 0);
    bool publish(const char *topic, const String &payload, bool retain = false) {
        return publish(topic, payload.c_str(), payload.length(), retain, 0);
    }
    bool subscribe(const char *topic, uint8_t qos = 0);

    // 需要在 loop 中周期调用：读包 + 保活
    void loop();

    int state() const { return _state; }

private:
    bool writeConnect(const char *clientId, const char *user, const char *pass,
                      const char *willTopic, const char *willMsg, uint16_t keepalive);
    bool writeHeader(uint8_t type_flags, uint32_t remLen);
    bool writeString(const char *s);
    int  readByte(uint32_t timeoutMs);
    bool readPacket(uint8_t *buf, size_t cap, size_t &outLen, uint32_t timeoutMs);
    void handle(uint8_t type, uint8_t *pkt, size_t len);
    uint32_t readVarInt(size_t &consumed);

    WiFiClient *_net = nullptr;
    MqttMsgCb   _cb;
    bool     _connected = false;
    uint16_t _keepalive = 30;
    uint16_t _nextPktId = 1;
    uint32_t _lastSend = 0;
    uint32_t _lastRecv = 0;
    int      _state = -1;      // CONNACK 返回码 / 错误码
    uint8_t  _rxBuf[MQTT_MAX_PACKET];
    uint8_t  _txBuf[MQTT_MAX_TX];
};
