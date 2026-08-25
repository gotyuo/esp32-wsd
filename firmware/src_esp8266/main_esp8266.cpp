// ============================================================
// EnvMon ESP8266 固件主程序
//
// 数据流: AHT20/BMP280 -> 采样 -> 阈值判定 -> LED -> MQTT
// OLED: 0.96" SSD1306 (u8g2 库, 软件 I2C, SDA=GPIO2/SCL=GPIO14)
//
// 串口调试命令(115200):
//   config   立即进入 AP 配网模式
//   factory  清除全部配置并重启
//   status   打印当前状态
// ============================================================
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <U8g2lib.h>
#include "pins_esp8266.h"
#include "config_store_esp8266.h"
#include "sensors_esp8266.h"
#include "alarm_esp8266.h"
#include "net_mgr_esp8266.h"
#include "mqtt_mgr_esp8266.h"

// u8g2 SSD1306 128x64, 软件 I2C: (rotation, clock=SCL, data=SDA, reset)
U8G2_SSD1306_128X64_NONAME_F_SW_I2C g_oled(U8G2_R0, /*clock=*/ PIN_OLED_SCL, /*data=*/ PIN_OLED_SDA, /*reset=*/ U8X8_PIN_NONE);
static bool        g_oledOk = false;
static SensorHub   g_sensors;
static AlarmDevice g_alarm;
static EnvData     g_last;
static uint32_t    g_lastRead = 0;
static uint32_t    g_lastPub  = 0;
static bool        g_mqttReady = false;
static uint32_t    g_lastOled = 0;

static void renderOled() {
    if (!g_oledOk) return;
    g_oled.clearBuffer();
    g_oled.setFont(u8g2_font_6x10_tr);

    // 标题行
    char line[24];
    snprintf(line, sizeof(line), "EnvMon v%s", FW_VERSION);
    g_oled.drawStr(2, 9, line);
    g_oled.drawStr(2, 19, "ESP8266");

    // WiFi 信号条
    int8_t rssi = g_net.wifiConnected() ? WiFi.RSSI() : 127;
    uint8_t bars = 0;
    if (rssi >= -50) bars = 4;
    else if (rssi >= -65) bars = 3;
    else if (rssi >= -78) bars = 2;
    else if (rssi >= -90) bars = 1;
    for (int i = 0; i < 4; i++) {
        uint8_t h = 2 + i * 2;
        if (i < bars) g_oled.drawBox(102 + i * 6, 17 - h, 4, h);
        else          g_oled.drawFrame(102 + i * 6, 17 - h, 4, h);
    }

    // 分隔线
    g_oled.drawHLine(2, 21, 124);

    // 温湿度气压
    g_oled.drawStr(2, 32, "Temp :");
    snprintf(line, sizeof(line), "%.1f", g_last.temp_c);
    g_oled.drawStr(52, 32, line);
    g_oled.drawStr(90, 32, "C");

    g_oled.drawStr(2, 43, "Hum  :");
    snprintf(line, sizeof(line), "%.1f", g_last.hum_pct);
    g_oled.drawStr(52, 43, line);
    g_oled.drawStr(90, 43, "%");

    g_oled.drawStr(2, 54, "Pres :");
    snprintf(line, sizeof(line), "%d", (int)g_last.pres_hpa);
    g_oled.drawStr(52, 54, line);
    g_oled.drawStr(90, 54, "hPa");

    // 底部状态
    g_oled.drawHLine(2, 57, 124);
    const char *wifiTxt = g_net.wifiConnected() ? "WIFI" : "wifi";
    const char *mqttTxt = g_mqttReady ? "MQTT" : "mqtt";
    g_oled.drawStr(2, 64, wifiTxt);
    g_oled.drawStr(40, 64, mqttTxt);
    snprintf(line, sizeof(line), "lvl:%d", (int)g_alarm.level());
    g_oled.drawStr(80, 64, line);

    g_oled.sendBuffer();
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

    // OLED: u8g2 初始化 (软件 I2C SCL=GPIO14, SDA=GPIO2)
    g_oled.begin();
    g_oled.clearBuffer();
    g_oled.setFont(u8g2_font_6x10_tr);
    g_oled.drawStr(2, 12, "EnvMon starting...");
    g_oled.drawStr(2, 26, "ESP8266 v" FW_VERSION);
    g_oled.sendBuffer();
    g_oledOk = true;
    Serial.println(F("[BOOT] OLED 0.96\" u8g2 OK (SDA=GPIO2 SCL=GPIO14)"));

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
        if (g_oledOk && (now - g_lastOled >= 500)) {
            g_lastOled = now;
            renderOled();
        }
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