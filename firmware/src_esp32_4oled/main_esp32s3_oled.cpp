// ============================================================
// EnvMon ESP32-S3 固件主程序 — 4 孔 I2C OLED 变体
// 屏幕：0.96" I2C OLED (SSD1306/SSD1315)，走 I2C1(GPIO13/14)
// 数据流：AHT20/BMP280(I2C0) -> 采样 -> 阈值判定 -> RGB LED/蜂鸣器
//         -> MQTT 上报 + OLED 显示
//
// 与 esp32-s3(TFT) 主版的区别：本变体使用 I2C OLED，无 ST7735 触摸屏 UI。
// 串口调试命令(115200)：config / factory / status
// ============================================================
#include <Arduino.h>
#include <WiFi.h>
#include "pins_esp32s3_oled.h"
#include "config_store.h"
#include "sensors.h"
#include "ssd1306.h"
#include "alarm.h"
#include "net_mgr.h"
#include "mqtt_mgr.h"
#include "ota_mgr.h"

static SensorHub g_sensors;
static Ssd1306   g_oled;
static bool      g_oledOk = false;
TwoWire          oledWire(1);  // I2C1 实例（bus_num=1，与传感器 I2C0 完全独立）
static AlarmDevice g_alarm;

static EnvData  g_last;
static uint32_t g_lastRead = 0;
static uint32_t g_lastOled = 0;
static uint32_t g_lastPub  = 0;
static bool     g_mqttReady = false;

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

static void renderOled() {
    if (!g_oledOk) return;
    g_oled.clear();
    g_oled.drawString(2, 0, "EnvMon v" FW_VERSION);
    g_oled.drawString(2, 8, "ESP32-S3 OLED");
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

void setup() {
    Serial.begin(115200);
    delay(400);
    Serial.println();
    Serial.println(F("======================================"));
    Serial.println(F(" EnvMon ESP32-S3 (OLED) firmware " FW_VERSION));
    Serial.println(F("======================================"));

    g_cfgStore.begin();
    bool saved = g_cfgStore.load(g_cfg);
    g_cfgStore.applyDefaults(g_cfg);
    Serial.printf("[BOOT] config %s, device_id=%s\n",
                  saved ? "loaded" : "NOT found (first boot)", g_cfg.device_id);

    // OLED: I2C1 独立总线；缺屏不阻断启动
    if (g_oled.begin(PIN_OLED_SCL, PIN_OLED_SDA, OLED_ADDR, &oledWire)) {
        g_oledOk = true;
        Serial.println(F("[BOOT] OLED 0.96\" OK (I2C1)"));
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

    g_alarm.begin();
    if (!g_sensors.begin()) {
        Serial.println(F("[BOOT] WARNING: sensors unavailable"));
    }

    g_net.setConfig(&g_cfg);
    g_net.begin();

    String _otaHost = String(g_cfg.mqtt_host) + ":" + String(g_cfg.mqtt_port);
    ota_set_server(_otaHost.length() > 1 ? _otaHost.c_str() : nullptr, nullptr);
    delay(500);
    ota_setup();
    ota_check(false);
    delay(1000);
}

void loop() {
    handleSerialCmd();
    g_net.loop();
    uint32_t now = millis();

    // ---------- AP 配网模式 ----------
    if (g_net.inAPMode()) {
        if (now - g_lastRead >= 2000) { g_lastRead = now; g_sensors.read(g_last); }
        g_alarm.update(AL_CONFIG, false);
        if (g_oledOk) {
            g_oled.clear();
            g_oled.drawString(4, 14, "AP config mode");
            g_oled.drawString(4, 30, g_net.apSSID().c_str());
            g_oled.drawString(4, 42, "connect via phone");
            g_oled.flush();
        }
        delay(10);
        return;
    }

    // ---------- MQTT 初始化 ----------
    if (!g_mqttReady && g_cfg.has_mqtt() && g_net.wifiConnected()) {
        g_mqtt.begin();
        g_mqttReady = true;
    }
    if (g_mqttReady) g_mqtt.loop();

    // ---------- LAN 自动发现 ----------
    if (!g_mqttReady && g_net.wifiConnected()
            && g_cfg.server_mode == 0 && !g_cfg.has_mqtt()) {
        if (!g_net.inDiscovery()) g_net.startDiscover();
        int disc = g_net.discoverLoop(now);
        if (disc == 1) {
            Serial.println(F("[MAIN] server discovered -> restarting to apply"));
            delay(500);
            ESP.restart();
        } else if (disc == -1) {
            Serial.println(F("[MAIN] discovery failed -> entering AP portal"));
            g_net.startAP();
        }
    }

    // ---------- 传感器采样（每 2s）----------
    if (now - g_lastRead >= 2000) {
        g_lastRead = now;
        g_sensors.read(g_last);
        g_sensors.readVitals(g_last);
    }
    AlarmLevel lvl = g_alarm.evaluate(g_last, g_cfg);
    g_alarm.update(lvl, g_cfg.alarm_sound);

    // ---------- OLED 刷新（每 500ms）----------
    if (g_oledOk && (now - g_lastOled >= 500)) {
        g_lastOled = now;
        renderOled();
    }

    // ---------- MQTT 周期上报 ----------
    if (g_mqttReady && g_mqtt.connected()
            && now - g_lastPub >= (uint32_t)g_cfg.report_interval * 1000UL) {
        g_lastPub = now;
        if (g_mqtt.publishTelemetry(g_last, (int)lvl)) {
            Serial.printf("[MAIN] telemetry published (t=%.1f h=%.1f p=%.1f)\n",
                          g_last.temp_c, g_last.hum_pct, g_last.pres_hpa);
        }
    }
    delay(5);
}
