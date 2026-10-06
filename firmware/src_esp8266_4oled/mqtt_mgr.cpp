// ============================================================
// MQTT 通信模块 ESP8266 v3.0
// 复用 ESP32 版的 mqtt_client.cpp（WiFiClient 纯手写 MQTT）
// 无 MAX30102，无 pr_hr
// ============================================================
#include "mqtt_mgr.h"
#include <ESP8266HTTPClient.h>
#include "mqtt_client.h"
#include <ESP8266WiFi.h>

static MqttClient client;
static WiFiClient net;

static char topicTele[64];
static char topicStat[64];
static char topicCfg[80];
static char topicVitals[80];

MqttMgr g_mqtt;

void MqttMgr::begin() {
    snprintf(topicTele,   sizeof(topicTele),   "envmon/%s/telemetry", g_cfg.device_id);
    snprintf(topicStat,   sizeof(topicStat),   "envmon/%s/status",    g_cfg.device_id);
    snprintf(topicCfg,    sizeof(topicCfg),    "envmon/%s/config",    g_cfg.device_id);
    snprintf(topicVitals, sizeof(topicVitals), "envmon/%s/vitals",    g_cfg.device_id);

    client.setClient(&net);
    client.setCallback([](const char *topic, const uint8_t *payload, size_t len) {
        if (strcmp(topic, topicCfg) != 0) return;
        String json;
        json.reserve(len);
        for (size_t i = 0; i < len; i++) json += (char)payload[i];
        g_mqtt.applyConfigPayload(json);
    });
}

bool MqttMgr::connected() const {
    return client.connected();
}

bool MqttMgr::doConnect() {
    Serial.printf("[MQTT] Connecting to %s:%d ...\n", g_cfg.mqtt_host, g_cfg.mqtt_port);
    bool ok = client.connect(g_cfg.mqtt_host, g_cfg.mqtt_port, g_cfg.device_id,
                              g_cfg.mqtt_user, g_cfg.mqtt_pass,
                              topicStat, "offline", 30);
    if (ok) {
        Serial.println(F("[MQTT] Connected"));
        client.publish(topicStat, "online", 6, true, 0);
        client.subscribe(topicCfg, 1);
        _retryDelay = 2000;  // 重置重连间隔
    } else {
        Serial.printf("[MQTT] Failed, state=%d\n", client.state());
    }
    return ok;
}

void MqttMgr::ensureConn() {
    if (client.connected()) return;
    uint32_t now = millis();
    if (now - _lastTry < _retryDelay) return;
    _lastTry = now;
    _retryDelay = min((uint32_t)60000, _retryDelay * 2);
    doConnect();
}

void MqttMgr::loop() {
    if (!g_cfg.has_mqtt()) return;
    if (WiFi.status() != WL_CONNECTED) {
        // WiFi 断开 = MQTT TCP 必然失效。立即主动断开（复位 _connected），
        // 否则 WiFi 恢复后 MQTT 还自认为已连接、永不重连（“假在线”）。
        client.disconnect();
        return;
    }
    ensureConn();
    client.loop();
}

bool MqttMgr::publishTelemetry(const EnvData &d, int alarm_level) {
    if (!client.connected()) return false;
    uint32_t seq = (uint32_t)(millis() / 1000);
    char ip_str[16] = "";
    IPAddress localIP = WiFi.localIP();
    snprintf(ip_str, sizeof(ip_str), "%d.%d.%d.%d",
             localIP[0], localIP[1], localIP[2], localIP[3]);

    char buf[320];
    snprintf(buf, sizeof(buf),
        "{\"device_id\":\"%s\""
        ",\"seq\":%lu"
        ",\"t\":%s,\"h\":%s,\"p\":%s"
        ",\"rssi\":%d,\"uptime\":%lu,\"alarm\":%d"
        ",\"fw\":\"%s\",\"heap\":%u,\"ip\":\"%s\"}",
        g_cfg.device_id,
        (unsigned long)seq,
        isnan(d.temp_c)   ? "null" : String(d.temp_c, 2).c_str(),
        isnan(d.hum_pct)  ? "null" : String(d.hum_pct, 2).c_str(),
        isnan(d.pres_hpa) ? "null" : String(d.pres_hpa, 2).c_str(),
        WiFi.RSSI(),
        (unsigned long)(millis() / 1000),
        alarm_level,
        FW_VERSION,
        (unsigned)ESP.getFreeHeap(),
        ip_str);
    return client.publish(topicTele, buf, strlen(buf), false, 0);
}

bool MqttMgr::publishVitals(const EnvData &d) {
    if (!client.connected()) return false;
    char ip_str[16] = "";
    IPAddress localIP = WiFi.localIP();
    snprintf(ip_str, sizeof(ip_str), "%d.%d.%d.%d",
             localIP[0], localIP[1], localIP[2], localIP[3]);
    char buf[256];
    int n = snprintf(buf, sizeof(buf),
        "{\"device_id\":\"%s\""
        ",\"ts\":\"%s\""
        ",\"t\":%s,\"h\":%s,\"p\":%s"
        ",\"ip\":\"%s\"}",
        g_cfg.device_id,
        "",  // ts 由服务器补
        isnan(d.temp_c)   ? "null" : String(d.temp_c, 2).c_str(),
        isnan(d.hum_pct)  ? "null" : String(d.hum_pct, 2).c_str(),
        isnan(d.pres_hpa) ? "null" : String(d.pres_hpa, 2).c_str(),
        ip_str);
    return client.publish(topicVitals, buf, n, false, 0);
}

void MqttMgr::applyConfigPayload(const String &json) {
    Serial.printf("[MQTT] Config received: %s\n", json.c_str());
}


// =================== HTTP 上传回退 ===================
#ifndef WEB_PORT
#define WEB_PORT 12090
#endif

bool MqttMgr::httpUploadTelemetry(const EnvData &d, int alarm_level) {
    if (!g_cfg.has_mqtt()) return false;
    if (WiFi.status() != WL_CONNECTED) return false;

    HTTPClient http;
    char url[128];
    snprintf(url, sizeof(url), "http://%s:%d/api/telemetry", g_cfg.mqtt_host, WEB_PORT);

    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(5000);

    char ip_str[16] = "";
    IPAddress localIP = WiFi.localIP();
    snprintf(ip_str, sizeof(ip_str), "%d.%d.%d.%d",
             localIP[0], localIP[1], localIP[2], localIP[3]);

    uint32_t seq = (uint32_t)(millis() / 1000);
    char buf[320];
    int n = snprintf(buf, sizeof(buf),
        "{\"device_id\":\"%s\""
        ",\"seq\":%lu"
        ",\"t\":%s,\"h\":%s,\"p\":%s"
        ",\"rssi\":%d,\"alarm\":%d"
        ",\"fw\":\"%s\",\"heap\":%u,\"ip\":\"%s\"}",
        g_cfg.device_id,
        (unsigned long)seq,
        isnan(d.temp_c)   ? "null" : String(d.temp_c, 2).c_str(),
        isnan(d.hum_pct)  ? "null" : String(d.hum_pct, 2).c_str(),
        isnan(d.pres_hpa) ? "null" : String(d.pres_hpa, 2).c_str(),
        WiFi.RSSI(),
        alarm_level,
        FW_VERSION,
        (unsigned)ESP.getFreeHeap(),
        ip_str);

    int code = http.POST((uint8_t*)buf, n);
    http.end();

    if (code == 200) {
        Serial.printf("[HTTP] telemetry OK (seq=%lu)\n", (unsigned long)seq);
        return true;
    } else {
        Serial.printf("[HTTP] upload failed: %d\n", code);
        return false;
    }
}
