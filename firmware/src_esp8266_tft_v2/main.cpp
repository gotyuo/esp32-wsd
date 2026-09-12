// ============================================================
// EnvMon ESP8266 固件主程序 — 0.96" ST7735 TFT 变体
//
// 屏幕: 0.96" SPI TFT (ST7735S) 160x80, 软件 SPI
// 数据流: AHT20/BMP280/MAX30102(I2C) -> 采样 -> 阈值判定 -> MQTT
//         -> TFT 显示
//
// 串口命令(115200): config / factory / status
// ============================================================
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <SPI.h>
#include "pins.h"
#include "config_store.h"
#include "sensors.h"
#include "alarm.h"
#include "net_mgr.h"
#include "mqtt_mgr.h"
#include "st7735.h"

static SensorHub  g_sensors;
static ST7735     g_tft;
static bool       g_tftOk = false;
static AlarmDevice g_alarm;

static EnvData  g_last;
static uint32_t g_lastRead = 0;
static uint32_t g_lastTft  = 0;
static uint32_t g_lastPub  = 0;
static bool     g_mqttReady = false;
static char     g_lastSsid[33] = "";
static bool     g_dirty = false;

static void renderTft();
static void refreshTft() { g_lastTft = 0; }

static String getCurSsid() {
    if (WiFi.status() == WL_CONNECTED) {
        String s = WiFi.SSID();
        s.trim();
        if (!s.isEmpty()) return s;
    }
    if (g_net.inAPMode()) return "AP-CONFIG";
    if (g_cfg.has_wifi()) return String(g_cfg.wifi_ssid);
    return "";
}

static void checkSsidChanged() {
    String cur = getCurSsid();
    if (cur != String(g_lastSsid)) {
        strncpy(g_lastSsid, cur.c_str(), sizeof(g_lastSsid) - 1);
        g_lastSsid[sizeof(g_lastSsid) - 1] = '\0';
        g_dirty = true;
    }
}

static void renderTft() {
    if (!g_tftOk) return;
    g_tft.fillScreen(C_BLACK);

    // ---- 顶部状态栏 ----
    g_tft.setTextSize(1);
    g_tft.setTextColor(C_GRAY);
    String ssid = getCurSsid();
    if (ssid.isEmpty()) ssid = "---";
    if (ssid.length() > 14) ssid = ssid.substring(0, 14);
    g_tft.setCursor(2, 3);
    g_tft.print("SSID:");
    g_tft.print(ssid.c_str());

    // WiFi 信号条
    int8_t rssi = g_net.wifiConnected() ? WiFi.RSSI() : 127;
    uint8_t bars = 0;
    if (rssi >= -50) bars = 4;
    else if (rssi >= -65) bars = 3;
    else if (rssi >= -78) bars = 2;
    else if (rssi >= -90) bars = 1;
    g_tft.fillRect(138, 3, 2, 8, bars >= 1 ? C_GREEN : C_GRAY);
    g_tft.fillRect(136, 5, 2, 6, bars >= 2 ? C_GREEN : C_GRAY);
    g_tft.fillRect(134, 7, 2, 4, bars >= 3 ? C_GREEN : C_GRAY);
    g_tft.fillRect(132, 9, 2, 2, bars >= 4 ? C_GREEN : C_GRAY);

    // 分隔线
    for (int x = 0; x < 160; x += 2) g_tft.drawPixel(x, 12, C_GRAY);

    // ---- AP 配网模式 ----
    if (g_net.inAPMode()) {
        g_tft.setTextColor(C_YELLOW);
        g_tft.setCursor(4, 18);
        g_tft.print("AP config mode");
        g_tft.setTextColor(C_WHITE);
        g_tft.setCursor(4, 32);
        g_tft.print(g_net.apSSID().c_str());
        g_tft.setCursor(4, 44);
        g_tft.print("192.168.4.1");
        g_tft.setCursor(4, 56);
        g_tft.print("set WiFi+MQTT");
        g_dirty = false;
        return;
    }

    // ---- 主数据区 ----
    char buf[16];

    // 温度
    g_tft.setTextColor(C_ORANGE);
    g_tft.setCursor(4, 20);
    g_tft.print("T:");
    snprintf(buf, sizeof(buf), "%.1f", g_last.temp_c);
    g_tft.setTextColor(C_WHITE);
    g_tft.setCursor(20, 20);
    g_tft.print(buf);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(48, 20);
    g_tft.print("C");

    // 湿度
    g_tft.setTextColor(C_CYAN);
    g_tft.setCursor(4, 36);
    g_tft.print("H:");
    snprintf(buf, sizeof(buf), "%.1f", g_last.hum_pct);
    g_tft.setTextColor(C_WHITE);
    g_tft.setCursor(20, 36);
    g_tft.print(buf);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(48, 36);
    g_tft.print("%");

    // 气压
    g_tft.setTextColor(C_GREEN);
    g_tft.setCursor(4, 52);
    g_tft.print("P:");
    snprintf(buf, sizeof(buf), "%d", (int)g_last.pres_hpa);
    g_tft.setTextColor(C_WHITE);
    g_tft.setCursor(20, 52);
    g_tft.print(buf);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(48, 52);
    g_tft.print("hPa");

    // 血氧/心率
    g_tft.setTextColor(C_RED);
    g_tft.setCursor(80, 20);
    g_tft.print("SpO2:");
    snprintf(buf, sizeof(buf), isnan(g_last.sp_o2) ? "--" : "%d%%", (int)g_last.sp_o2);
    g_tft.setTextColor(C_WHITE);
    g_tft.setCursor(115, 20);
    g_tft.print(buf);

    g_tft.setTextColor(C_YELLOW);
    g_tft.setCursor(80, 36);
    g_tft.print("PR:");
    snprintf(buf, sizeof(buf), isnan(g_last.pr_hr) ? "--" : "%d", (int)g_last.pr_hr);
    g_tft.setTextColor(C_WHITE);
    g_tft.setCursor(115, 36);
    g_tft.print(buf);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(130, 36);
    g_tft.print("bpm");

    // ---- 底部状态栏 ----
    for (int x = 0; x < 160; x += 2) g_tft.drawPixel(x, 70, C_GRAY);
    g_tft.setTextSize(1);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(4, 72);
    const char *net = g_mqttReady ? "MQTT" : (g_net.wifiConnected() ? "WIFI" : "OFF");
    g_tft.print(net);
    g_tft.print(" v");
    g_tft.print(FW_VER);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(100, 72);
    g_tft.print("L:");
    g_tft.print(g_alarm.level());

    // 报警红色边框
    if (g_alarm.level() >= AL_ALARM) {
        for (int x = 0; x < 160; x += 2) { g_tft.drawPixel(x, 0, C_RED); g_tft.drawPixel(x, 79, C_RED); }
        for (int y = 0; y < 80; y += 2)  { g_tft.drawPixel(0, y, C_RED); g_tft.drawPixel(159, y, C_RED); }
    }
    g_dirty = false;
}

static void handleSerialCmd() {
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toLowerCase();
    if (cmd == "config") {
        Serial.println(F("[CMD] AP config mode"));
        g_net.startAP();
        refreshTft();
    } else if (cmd == "factory") {
        Serial.println(F("[CMD] Factory reset"));
        if (g_tftOk) {
            g_tft.fillScreen(C_BLACK);
            g_tft.setTextColor(C_RED);
            g_tft.setTextSize(2);
            g_tft.setCursor(20, 28);
            g_tft.print("FACTORY");
            g_tft.setCursor(24, 44);
            g_tft.print("RESET");
            delay(1500);
        }
        g_cfgStore.clear();
        delay(300);
        ESP.restart();
    } else if (cmd == "status") {
        Serial.printf("[CMD] wifi=%d mqtt=%d heap=%u t=%.1f h=%.1f p=%.1f spo2=%.1f pr=%.1f\n",
                      (WiFi.status() == WL_CONNECTED), g_mqtt.connected(),
                      (unsigned)ESP.getFreeHeap(),
                      g_last.temp_c, g_last.hum_pct, g_last.pres_hpa,
                      g_last.sp_o2, g_last.pr_hr);
    }
}

void setup() {
    Serial.begin(115200);
    delay(400);
    Serial.println();
    Serial.println(F("===================================="));
    Serial.printf(" EnvMon ESP8266 TFT v%s\n", FW_VERSION);
    Serial.println(F("===================================="));

    g_cfgStore.begin();
    bool saved = g_cfgStore.load(g_cfg);
    g_cfgStore.applyDefaults(g_cfg);
    Serial.printf("[BOOT] config %s, id=%s\n",
                  saved ? "loaded" : "NOT found (first boot)", g_cfg.device_id);

    g_alarm.begin();

    // 传感器
    if (!g_sensors.begin()) {
        Serial.println(F("[BOOT] WARNING: no sensors"));
    }

    // TFT
    g_tft.begin(PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_RST, PIN_TFT_MOSI, PIN_TFT_SCK);
    g_tft.fillScreen(C_BLACK);
    g_tft.setTextColor(C_WHITE);
    g_tft.setTextSize(2);
    g_tft.setCursor(30, 26);
    g_tft.print("ENVMON");
    g_tft.setTextSize(1);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(40, 50);
    g_tft.print("v");
    g_tft.print(FW_VER);
    g_tftOk = true;

    // 背光
    if (PIN_TFT_BL < 255) {
        pinMode(PIN_TFT_BL, OUTPUT);
        digitalWrite(PIN_TFT_BL, HIGH);
    }
    Serial.printf("[BOOT] TFT OK CS=%d DC=%d RST=%d MOSI=%d SCK=%d\n",
                  PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_RST, PIN_TFT_MOSI, PIN_TFT_SCK);

    g_net.setConfig(&g_cfg);
    g_net.begin();

    // 启动页
    delay(200);
    if (g_tftOk) {
        g_tft.fillScreen(C_BLACK);
        g_tft.setTextColor(C_WHITE);
        g_tft.setCursor(20, 20);
        g_tft.print("Connecting...");
        String bs = getCurSsid();
        if (bs.isEmpty()) bs = "---";
        if (bs.length() > 20) bs = bs.substring(0, 20);
        g_tft.setCursor(10, 40);
        g_tft.print("SSID:");
        g_tft.print(bs.c_str());
        g_tft.setTextColor(C_GRAY);
        g_tft.setCursor(20, 60);
        g_tft.print("EnvMon v");
        g_tft.print(FW_VER);
    }
    Serial.printf("[BOOT] target SSID: %s\n", getCurSsid().c_str());
    delay(2000);
    refreshTft();
}

void loop() {
    handleSerialCmd();
    g_net.loop();
    checkSsidChanged();
    uint32_t now = millis();

    // AP 配网模式
    if (g_net.inAPMode()) {
        if (g_tftOk && (now - g_lastTft >= 2000 || g_dirty)) {
            g_lastTft = now;
            renderTft();
        }
        if (now - g_lastRead >= 2000) {
            g_lastRead = now;
            g_sensors.read(g_last);
            g_sensors.readVitals(g_last);
        }
        delay(10);
        return;
    }

    // MQTT
    if (!g_mqttReady && g_cfg.has_mqtt() && g_net.wifiConnected()) {
        g_mqtt.begin();
        g_mqttReady = true;
    }
    if (g_mqttReady) g_mqtt.loop();

    // 传感器
    if (now - g_lastRead >= 2000) {
        g_lastRead = now;
        g_sensors.read(g_last);
        g_sensors.readVitals(g_last);
    }

    // 报警判定
    AlarmLevel lvl = g_alarm.evaluate(g_last, g_cfg);

    // TFT 刷新
    if (g_tftOk && (now - g_lastTft >= 2000 || g_dirty)) {
        g_lastTft = now;
        renderTft();
    }

    // MQTT 上报
    if (g_mqttReady && g_mqtt.connected() &&
        now - g_lastPub >= (uint32_t)g_cfg.report_interval * 1000UL) {
        g_lastPub = now;
        if (g_mqtt.publishTelemetry(g_last, (int)lvl)) {
            Serial.printf("[MAIN] telemetry (t=%.1f h=%.1f p=%.1f spo2=%.1f pr=%.1f)\n",
                          g_last.temp_c, g_last.hum_pct, g_last.pres_hpa,
                          g_last.sp_o2, g_last.pr_hr);
        }
    }
    delay(5);
}