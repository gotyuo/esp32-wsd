// ============================================================
// MQTT 通信模块（使用内置 MqttClient，无第三方依赖）
//  - 上报: envmon/{id}/telemetry   传感器数据（JSON）
//  - 状态: envmon/{id}/status      在线/离线（遗嘱消息）
//  - 订阅: envmon/{id}/config      服务器下发阈值与参数
//  - 回执: envmon/{id}/config/ack  配置生效确认
//  断线自动重连（指数退避）
// ============================================================
#include "mqtt_mgr.h"
#include "net_mgr.h"
#include "mqtt_client.h"
#include <WiFi.h>

MqttMgr g_mqtt;

static WiFiClient _net;
static MqttClient _client;

static char _topicTele[64];
static char _topicStat[64];
static char _topicCfg[64];
static char _topicAck[64];
static char _topicReq[64];

// ---------------- 迷你 JSON 解析（服务器下发的扁平配置） ----------------
static bool jsonFindKey(const String &json, const char *key, int &pos) {
    String pat = String("\"") + key + "\"";
    int p = json.indexOf(pat);
    if (p < 0) return false;
    p += pat.length();
    while (p < (int)json.length() && json.charAt(p) != ':') p++;
    if (p >= (int)json.length()) return false;
    pos = p + 1;
    return true;
}

static bool jsonGetFloat(const String &json, const char *key, float &out) {
    int p;
    if (!jsonFindKey(json, key, p)) return false;
    while (p < (int)json.length() && (json.charAt(p) == ' ' || json.charAt(p) == '\t')) p++;
    const char *s = json.c_str() + p;
    char *end = nullptr;
    float v = strtof(s, &end);
    if (end == s) return false;
    out = v;
    return true;
}

static bool jsonGetInt(const String &json, const char *key, int &out) {
    float f;
    if (!jsonGetFloat(json, key, f)) return false;
    out = (int)f;
    return true;
}

static bool jsonGetBool(const String &json, const char *key, bool &out) {
    int p;
    if (!jsonFindKey(json, key, p)) return false;
    while (p < (int)json.length() && json.charAt(p) == ' ') p++;
    if (json.charAt(p) == 't' || json.charAt(p) == '1') { out = true;  return true; }
    if (json.charAt(p) == 'f' || json.charAt(p) == '0') { out = false; return true; }
    return false;
}

// ---------------- 生命周期 ----------------
void MqttMgr::begin() {
    snprintf(_topicTele, sizeof(_topicTele), "envmon/%s/telemetry",  g_cfg.device_id);
    snprintf(_topicStat, sizeof(_topicStat), "envmon/%s/status",     g_cfg.device_id);
    snprintf(_topicCfg,  sizeof(_topicCfg),  "envmon/%s/config",     g_cfg.device_id);
    snprintf(_topicAck,  sizeof(_topicAck),  "envmon/%s/config/ack", g_cfg.device_id);
    snprintf(_topicReq,  sizeof(_topicReq),  "envmon/%s/config/req", g_cfg.device_id);

    _client.setClient(&_net);
    _client.setCallback([](const char *topic, const uint8_t *payload, size_t len) {
        if (strcmp(topic, _topicCfg) != 0) return;
        String json;
        json.reserve(len);
        for (size_t i = 0; i < len; i++) json += (char)payload[i];
        g_mqtt.applyConfigPayload(json);
    });
}

bool MqttMgr::connected() const {
    return _client.connected();
}

bool MqttMgr::doConnect() {
    Serial.printf("[MQTT] Connecting to %s:%d ...\n", g_cfg.mqtt_host, g_cfg.mqtt_port);
    bool ok = _client.connect(g_cfg.mqtt_host, g_cfg.mqtt_port, g_cfg.device_id,
                              g_cfg.mqtt_user, g_cfg.mqtt_pass,
                              _topicStat, "offline", 30);
    if (ok) {
        Serial.println(F("[MQTT] Connected"));
        _client.publish(_topicStat, "online", 6, true, 0);   // 在线状态(保留)
        _client.subscribe(_topicCfg, 1);
        _client.publish(_topicReq, "{}", 2, false, 0);       // 请求最新配置
        _retryDelay = 2000;
    } else {
        Serial.printf("[MQTT] Failed, state=%d\n", _client.state());
    }
    return ok;
}

void MqttMgr::ensureConn() {
    if (_client.connected()) return;
    uint32_t now = millis();
    if (now - _lastTry < _retryDelay) return;
    _lastTry = now;
    _retryDelay = min((uint32_t)60000, _retryDelay * 2);
    doConnect();
}

void MqttMgr::loop() {
    if (!g_cfg.has_mqtt() || !g_net.wifiConnected()) return;
    ensureConn();
    _client.loop();
}

bool MqttMgr::publishTelemetry(const EnvData &d, int alarm_level) {
    if (!_client.connected()) return false;

    char buf[256];
    int n = snprintf(buf, sizeof(buf),
        "{\"device_id\":\"%s\""
        ",\"t\":%s,\"h\":%s,\"p\":%s"
        ",\"rssi\":%d,\"uptime\":%lu,\"alarm\":%d"
        ",\"fw\":\"%s\",\"heap\":%u}",
        g_cfg.device_id,
        isnan(d.temp_c)   ? "null" : String(d.temp_c, 2).c_str(),
        isnan(d.hum_pct)  ? "null" : String(d.hum_pct, 2).c_str(),
        isnan(d.pres_hpa) ? "null" : String(d.pres_hpa, 2).c_str(),
        WiFi.RSSI(),
        (unsigned long)(millis() / 1000),
        alarm_level,
        FW_VERSION,
        (unsigned)ESP.getFreeHeap());

    return _client.publish(_topicTele, buf, (size_t)n, false, 0);
}

void MqttMgr::applyConfigPayload(const String &json) {
    if (json.length() < 2 || json.indexOf('{') < 0) {
        Serial.println(F("[MQTT] Bad config payload"));
        return;
    }
    Serial.printf("[MQTT] Config received: %s\n", json.c_str());

    float f; int iv; bool b;
    if (jsonGetFloat(json, "temp_min", f)) g_cfg.temp_min = f;
    if (jsonGetFloat(json, "temp_max", f)) g_cfg.temp_max = f;
    if (jsonGetFloat(json, "hum_min", f))  g_cfg.hum_min  = f;
    if (jsonGetFloat(json, "hum_max", f))  g_cfg.hum_max  = f;
    if (jsonGetFloat(json, "pres_min", f)) g_cfg.pres_min = f;
    if (jsonGetFloat(json, "pres_max", f)) g_cfg.pres_max = f;
    if (jsonGetInt(json, "report_interval", iv) && iv >= 3) g_cfg.report_interval = iv;
    if (jsonGetBool(json, "alarm_enabled", b)) g_cfg.alarm_enabled = b;
    if (jsonGetBool(json, "alarm_sound", b))   g_cfg.alarm_sound   = b;

    g_cfgStore.save(g_cfg);   // 持久化，掉电保持
    _client.publish(_topicAck, "ok");
}
