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
#include "ssd1306.h"

static SensorHub   g_sensors;
static Ssd1306     g_oled;
static bool        g_oledOk = false;
static AlarmDevice g_alarm;
static EnvData     g_last;
static uint32_t    g_lastRead = 0;
static uint32_t    g_lastPub  = 0;
static bool        g_mqttReady = false;
static uint32_t    g_lastOled = 0;

static void renderOled() {
    if (!g_oledOk) return;
    g_oled.clear();
    g_oled.drawString(2, 0, "EnvMon v" FW_VERSION);
    g_oled.drawString(2, 8, "ESP8266");
    int8_t rssi = g_net.wifiConnected() ? WiFi.RSSI() : 127;
    uint8_t bars = 0;
    if (rssi >= -50) bars = 4;
    else if (rssi >= -65) bars = 3;
    else if (rssi >= -78) bars = 2;
    else if (rssi >= -90) bars = 1;
    g_oled.drawWifiBars(104, 1, bars);
    g_oled.drawLineH(2, 17, OLED_W - 4, 1);
    g_oled.drawString(2, 20, "Temp :");
    g_oled.drawNumFP(58, 20, g_last.temp_c, 1);
    g_oled.drawString(96, 20, "C");
    g_oled.drawString(2, 29, "Hum  :");
    g_oled.drawNumFP(58, 29, g_last.hum_pct, 1);
    g_oled.drawString(96, 29, "%");
    g_oled.drawString(2, 38, "Pres :");
    g_oled.drawNum(58, 38, (int32_t)g_last.pres_hpa);
    g_oled.drawString(96, 38, "hPa");
    g_oled.drawLineH(2, 47, OLED_W - 4, 1);
    const char *wifiTxt = g_net.wifiConnected() ? "WiFi OK" : "No WiFi";
    const char *mqttTxt = g_mqttReady ? "MQTT OK" : "MQTT -";
    g_oled.drawString(2, 50, wifiTxt);
    g_oled.drawString(2, 58, mqttTxt);
    g_oled.drawString(70, 58, "lvl:");
    g_oled.drawNum(96, 58, (int32_t)g_alarm.level());
    g_oled.flush();
}

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
    if (g_oled.beginSoftware(PIN_OLED_SCL, PIN_OLED_SDA, OLED_ADDR)) {
        g_oledOk = true;
        Serial.println(F("[BOOT] OLED 0.96\" OK"));
    } else {
        g_oledOk = false;
        Serial.println(F("[BOOT] OLED not found (continue without screen)"));
    }
    if (g_oledOk) {
        g_oled.clear();
        g_oled.drawString(4, 20, "EnvMon starting...");
        g_oled.drawString(4, 32, "please wait");
        g_oled.flush();
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
    if (g_oledOk && (now - g_lastOled >= 500)) {
        g_lastOled = now;
        renderOled();
    }

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
