#include "mqtt_client.h"

// ---------------- 发送辅助 ----------------
static size_t _txLen;   // 在函数内使用

bool MqttClient::writeHeader(uint8_t type_flags, uint32_t remLen) {
    _txLen = 0;
    _txBuf[_txLen++] = type_flags;
    // 变长整数（remaining length）
    do {
        uint8_t b = remLen & 0x7F;
        remLen >>= 7;
        if (remLen > 0) b |= 0x80;
        _txBuf[_txLen++] = b;
    } while (remLen > 0);
    return true;
}

bool MqttClient::writeString(const char *s) {
    uint16_t n = strlen(s);
    _txBuf[_txLen++] = n >> 8;
    _txBuf[_txLen++] = n & 0xFF;
    memcpy(&_txBuf[_txLen], s, n);
    _txLen += n;
    return true;
}

// ---------------- CONNECT ----------------
bool MqttClient::writeConnect(const char *clientId, const char *user, const char *pass,
                              const char *willTopic, const char *willMsg, uint16_t keepalive) {
    // 先计算变长头 + 负载长度
    uint32_t rem = 2 + 4 + 1 + 1 + 2;   // 协议名(2+4) + 级别(1) + 标志(1) + keepalive(2)
    rem += 2 + strlen(clientId);
    bool hasWill = willTopic && willMsg;
    if (hasWill) { rem += 2 + strlen(willTopic); rem += 2 + strlen(willMsg); }
    bool hasUser = user && user[0];
    bool hasPass = pass && pass[0];
    if (hasUser) rem += 2 + strlen(user);
    if (hasPass) rem += 2 + strlen(pass);

    writeHeader(0x10, rem);

    // 协议名 "MQTT"
    _txBuf[_txLen++] = 0x00; _txBuf[_txLen++] = 0x04;
    _txBuf[_txLen++] = 'M'; _txBuf[_txLen++] = 'Q'; _txBuf[_txLen++] = 'T'; _txBuf[_txLen++] = 'T';
    _txBuf[_txLen++] = 0x04;   // 协议级别 3.1.1

    uint8_t flags = 0x02;      // clean session
    if (hasWill) { flags |= 0x04; flags |= (0x01 << 5); /* will retain */ }
    if (hasUser) flags |= 0x80;
    if (hasPass) flags |= 0x40;
    _txBuf[_txLen++] = flags;

    _txBuf[_txLen++] = keepalive >> 8;
    _txBuf[_txLen++] = keepalive & 0xFF;

    writeString(clientId);
    if (hasWill) { writeString(willTopic); writeString(willMsg); }
    if (hasUser) writeString(user);
    if (hasPass) writeString(pass);

    return _net->write(_txBuf, _txLen) == _txLen;
}

// ---------------- 连接流程 ----------------
bool MqttClient::connect(const char *host, uint16_t port, const char *clientId,
                         const char *user, const char *pass,
                         const char *willTopic, const char *willMsg,
                         uint16_t keepalive) {
    _keepalive = keepalive;
    _state = -1;
    _connected = false;

    _net->stop();
    if (!_net->connect(host, port, 5000)) {
        _state = -2;   // TCP 连接失败
        return false;
    }
    _net->setTimeout(3);

    if (!writeConnect(clientId, user, pass, willTopic, willMsg, keepalive)) {
        _net->stop();
        return false;
    }
    _lastSend = millis();
    _lastRecv = millis();

    // 等待 CONNACK
    uint32_t t0 = millis();
    while (millis() - t0 < 5000) {
        if (_net->available() >= 4) {
            uint8_t hdr = _net->read();
            uint8_t rem = _net->read();
            uint8_t f1  = _net->read();
            uint8_t rc  = _net->read();
            (void)f1;
            if ((hdr & 0xF0) == 0x20 && rem == 2) {
                _state = rc;
                if (rc == 0) {
                    _connected = true;
                    return true;
                }
            }
            _net->stop();
            return false;
        }
        delay(5);
    }
    _net->stop();
    _state = -3;   // CONNACK 超时
    return false;
}

void MqttClient::disconnect() {
    if (_net->connected()) {
        uint8_t pkt[2] = {0xE0, 0x00};
        _net->write(pkt, 2);
    }
    _net->stop();
    _connected = false;
}

bool MqttClient::connected() {
    return _connected && _net->connected();
}

// ---------------- 接收 ----------------
int MqttClient::readByte(uint32_t timeoutMs) {
    uint32_t t0 = millis();
    while (!_net->available()) {
        if (millis() - t0 >= timeoutMs) return -1;
        delay(1);
    }
    return _net->read();
}

bool MqttClient::readPacket(uint8_t *buf, size_t cap, size_t &outLen, uint32_t timeoutMs) {
    int hdr = readByte(timeoutMs);
    if (hdr < 0) return false;
    buf[0] = (uint8_t)hdr;

    // 读取变长 remaining length
    uint32_t rem = 0;
    uint32_t mult = 1;
    size_t pos = 1;
    for (int i = 0; i < 4; i++) {
        int b = readByte(timeoutMs);
        if (b < 0) return false;
        buf[pos++] = (uint8_t)b;
        rem += (b & 0x7F) * mult;
        mult *= 128;
        if (!(b & 0x80)) break;
    }

    if (rem > cap - pos) {
        // 包过大：丢弃负载
        for (uint32_t i = 0; i < rem; i++) {
            if (readByte(timeoutMs) < 0) break;
        }
        outLen = 0;
        return false;
    }
    for (uint32_t i = 0; i < rem; i++) {
        int b = readByte(timeoutMs);
        if (b < 0) return false;
        buf[pos++] = (uint8_t)b;
    }
    outLen = pos;
    return true;
}

void MqttClient::handle(uint8_t type, uint8_t *pkt, size_t len) {
    switch (type) {
    case 0x30: {   // PUBLISH
        // 解析剩余部分
        size_t i = 1;
        uint32_t rem = 0, mult = 1;
        for (; i < len; i++) {
            uint8_t b = pkt[i];
            rem += (b & 0x7F) * mult; mult *= 128;
            if (!(b & 0x80)) { i++; break; }
        }
        if (i >= len) return;
        size_t bodyStart = i;
        uint16_t topicLen = (pkt[i] << 8) | pkt[i + 1];
        i += 2;
        if (i + topicLen > len) return;
        char topic[128];
        size_t tc = topicLen < sizeof(topic) - 1 ? topicLen : sizeof(topic) - 1;
        memcpy(topic, &pkt[i], tc);
        topic[tc] = 0;
        i += topicLen;
        uint8_t qos = (pkt[0] >> 1) & 0x03;
        uint16_t pktId = 0;
        if (qos > 0) {
            if (i + 2 > len) return;
            pktId = (pkt[i] << 8) | pkt[i + 1];
            i += 2;
        }
        size_t payloadLen = (bodyStart + rem) > i ? (bodyStart + rem - i) : 0;
        if (payloadLen > 0 && _cb) _cb(topic, &pkt[i], payloadLen);
        // QoS1 回 PUBACK
        if (qos == 1) {
            uint8_t ack[4] = {0x40, 0x02, (uint8_t)(pktId >> 8), (uint8_t)(pktId & 0xFF)};
            _net->write(ack, 4);
            _lastSend = millis();
        }
        break;
    }
    case 0x90:   // SUBACK
        break;
    case 0xD0:   // PINGRESP
        break;
    case 0x40:   // PUBACK
        break;
    default:
        break;
    }
}

void MqttClient::loop() {
    if (!connected()) return;

    // 处理所有可读数据
    while (_net->available()) {
        size_t outLen = 0;
        if (!readPacket(_rxBuf, sizeof(_rxBuf), outLen, 50)) break;
        if (outLen > 0) {
            _lastRecv = millis();
            handle(_rxBuf[0] & 0xF0, _rxBuf, outLen);
        }
    }

    // 保活 ping
    if (millis() - _lastSend >= (uint32_t)_keepalive * 500UL) {
        uint8_t ping[2] = {0xC0, 0x00};
        _net->write(ping, 2);
        _lastSend = millis();
    }
}

// ---------------- 发布 / 订阅 ----------------
bool MqttClient::publish(const char *topic, const char *payload, size_t len,
                         bool retain, uint8_t qos) {
    if (!connected()) return false;
    uint32_t rem = 2 + strlen(topic) + len + (qos > 0 ? 2 : 0);
    if (rem + 5 > MQTT_MAX_TX) return false;

    uint8_t type = 0x30 | (qos << 1) | (retain ? 1 : 0);
    writeHeader(type, rem);
    writeString(topic);
    if (qos > 0) {
        _txBuf[_txLen++] = _nextPktId >> 8;
        _txBuf[_txLen++] = _nextPktId & 0xFF;
        _nextPktId = (_nextPktId + 1) & 0x7FFF;
        if (_nextPktId == 0) _nextPktId = 1;
    }
    memcpy(&_txBuf[_txLen], payload, len);
    _txLen += len;

    bool ok = _net->write(_txBuf, _txLen) == _txLen;
    _lastSend = millis();
    return ok;
}

bool MqttClient::subscribe(const char *topic, uint8_t qos) {
    if (!connected()) return false;
    uint32_t rem = 2 + 2 + strlen(topic) + 1;
    if (rem + 5 > MQTT_MAX_TX) return false;
    writeHeader(0x82, rem);
    _txBuf[_txLen++] = _nextPktId >> 8;
    _txBuf[_txLen++] = _nextPktId & 0xFF;
    _nextPktId = (_nextPktId + 1) & 0x7FFF;
    if (_nextPktId == 0) _nextPktId = 1;
    writeString(topic);
    _txBuf[_txLen++] = qos;
    bool ok = _net->write(_txBuf, _txLen) == _txLen;
    _lastSend = millis();
    return ok;
}
