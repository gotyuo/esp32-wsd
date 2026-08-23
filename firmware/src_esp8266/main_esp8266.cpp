// ============================================================
// EnvMon ESP8266 固件主程序
//
// 数据流: AHT20/BMP280 -> 采样 -> 阈值判定 -> LED/蜂鸣器 -> MQTT
//
// 串口调试命令(115200):
//   config   立即进入 AP 配网模式
//   factory  清除全部配置并重启
//   status   打印当前状态
// ============================================================
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include "pins_esp8266.h"
#include "config_store_esp8266.h"
#include "sensors_esp8266.h"
#include "alarm_esp8266.h"
#include "net_mgr_esp8266.h"
#include "mqtt_mgr_esp8266.h"

static SensorHub   g_sensors;
static AlarmDevice g_alarm;
static EnvData     g_last;
static uint32_t    g_lastRead = 0;
static uint32_t    g_lastPub  = 0;
static bool        g_mqttReady = false;

static void handleSerialCmd() {
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toLowerCase();
    if (cmd == "config") {
        Serial.println(F("[CMD] Entering AP config mode..."));
        g_net.startAP();
    } else if (cmd == "factory") {
        Serial.println(F("[CMD] Factory reset..."));
        g_cfgStore.clear();
        delay(300);
        ESP.restart();
    } else if (cmd == "status") {
        Serial.printf("[CMD] wifi=%d mqtt=%d heap=%u t=%.1f h=%.1f p=%.1f\n",
                      (WiFi.status() == WL_CONNECTED), g_mqtt.connected(),
                      (unsigned)ESP.getFreeHeap(),
                      g_last.temp_c, g_last.hum_pct, g_last.pres_hpa);
    }
}

void setup() {
    Serial.begin(115200);
    delay(400);
    Serial.println();
    Serial.println(F("======================================"));
    Serial.println(F(" EnvMon ESP8266 firmware " FW_VERSION));
    Serial.println(F("======================================"));

    g_cfgStore.begin();
    bool saved = g_cfgStore.load(g_cfg);
    g_cfgStore.applyDefaults(g_cfg);
    Serial.printf("[BOOT] config %s, device_id=%s\n",
                  saved ? "loaded" : "NOT found (first boot)", g_cfg.device_id);

    g_alarm.begin();
    if (!g_sensors.begin()) {
        Serial.println(F("[BOOT] WARNING: sensors unavailable"));
    }
    g_net.setConfig(&g_cfg);
    g_net.begin();
    delay(1000);
}

void loop() {
    handleSerialCmd();
    g_net.loop();
    uint32_t now = millis();

    // ---------- AP 配网模式 ----------
    if (g_net.inAPMode()) {
        if (now - g_lastRead >= 2000) {
            g_lastRead = now;
            g_sensors.read(g_last);
        }
        g_alarm.update(AL_CONFIG, false);
        delay(10);
        return;
    }

    // ---------- MQTT 初始化 ----------
    if (!g_mqttReady && g_cfg.has_mqtt() && g_net.wifiConnected()) {
        g_mqtt.begin();
        g_mqttReady = true;
    }
    if (g_mqttReady) g_mqtt.loop();

    // ---------- 传感器采样（每 2s）----------
    if (now - g_lastRead >= 2000) {
        g_lastRead = now;
        g_sensors.read(g_last);
    }
    AlarmLevel lvl = g_alarm.evaluate(g_last, g_cfg);
    g_alarm.update(lvl, g_cfg.alarm_sound);

    // ---------- MQTT 周期上报 ----------
    if (g_mqttReady && g_mqtt.connected() &&
        now - g_lastPub >= (uint32_t)g_cfg.report_interval * 1000UL) {
        g_lastPub = now;
        if (g_mqtt.publishTelemetry(g_last, (int)lvl)) {
            Serial.printf("[MAIN] telemetry published (t=%.1f h=%.1f p=%.1f)\n",
                          g_last.temp_c, g_last.hum_pct, g_last.pres_hpa);
        }
    }
    delay(5);
}
